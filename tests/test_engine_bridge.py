"""Protocol expiry and Engine outage preserve the local fallback."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 7, 10, tzinfo=timezone(timedelta(hours=2)))


class Response:
    content_length = 100

    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.result


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, result):
        capture = {}
        def post(url, **kwargs):
            capture.update(url=url, **kwargs)
            if isinstance(result, Exception):
                raise result
            return Response(result)
        source = (ROOT / "custom_components/casa_es_energy_manager/engine_bridge.py").read_text()
        functions = ast.Module(body=[n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef)], type_ignores=[])
        namespace = {"asyncio": asyncio, "dt_util": SimpleNamespace(parse_datetime=datetime.fromisoformat),
                     "async_get_clientsession": lambda hass: SimpleNamespace(post=post)}
        exec(compile(functions, "engine_bridge.py", "exec"), namespace)
        return namespace["async_plan"], capture

    async def call(self, result):
        bridge, capture = self.bridge(result)
        answer = await bridge(None, {"engine_url": "http://engine:8099", "engine_token": "private-token", "reviewed": False}, [], {}, NOW)
        return answer, capture

    async def test_valid_plan_and_credential_is_not_in_snapshot(self):
        result = {"schema": 1, "expires_at": (NOW + timedelta(seconds=120)).isoformat(), "zones": {}, "state": "NORMAL"}
        answer, capture = await self.call(result)
        self.assertEqual(answer, result)
        self.assertNotIn("engine_token", capture["json"]["house"])
        self.assertEqual(capture["headers"]["Authorization"], "Bearer private-token")

    async def test_expired_future_unbounded_and_naive_plans_rejected(self):
        for expiry in (NOW - timedelta(seconds=1), NOW + timedelta(hours=1), NOW.replace(tzinfo=None)):
            answer, _ = await self.call({"schema": 1, "expires_at": expiry.isoformat(), "zones": {}})
            self.assertEqual(answer["state"], "DEGRADED")
            self.assertEqual(answer["zones"], {})

    async def test_network_outage_does_not_escape_to_coordinator(self):
        answer, _ = await self.call(ConnectionError("unreachable"))
        self.assertEqual(answer["state"], "DEGRADED")
        self.assertIn("engine_unavailable_local_fallback", answer["reasons"])

    async def test_missing_engine_configuration_uses_local_fallback(self):
        bridge, capture = self.bridge(None)
        answer = await bridge(None, {}, [], {}, NOW)
        self.assertEqual(answer["reasons"], ["engine_not_configured"])
        self.assertEqual(capture, {})

