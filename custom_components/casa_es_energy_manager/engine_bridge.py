"""Internal numerical planner client; no remote device command is accepted."""
import asyncio
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util


async def async_plan(hass, house, zones, energy, now):
    url = house.get("engine_url", "").rstrip("/")
    token = house.get("engine_token", "")
    if not url or not token:
        return {"state": "DEGRADED", "reasons": ["engine_not_configured"], "zones": {}}
    try:
        async with asyncio.timeout(5):
            async with async_get_clientsession(hass).post(url + "/v1/plan",
                headers={"Authorization": "Bearer " + token},
                json={"schema": 1, "timestamp": now.isoformat(),
                      "house": {k: v for k, v in house.items() if k not in ("engine_token", "engine_url")},
                      "zones": zones, "energy": energy}) as response:
                response.raise_for_status()
                if response.content_length and response.content_length > 1048576:
                    raise ValueError("oversize")
                result = await response.json()
        expires = dt_util.parse_datetime(result.get("expires_at", ""))
        if result.get("schema") != 1 or not expires or expires.tzinfo is None or not now.timestamp() < expires.timestamp() <= now.timestamp() + 180:
            raise ValueError("stale_or_invalid_plan")
        if not isinstance(result.get("zones"), dict):
            raise ValueError("invalid_zones")
        return result
    except Exception:
        return {"state": "DEGRADED", "reasons": ["engine_unavailable_local_fallback"], "zones": {}}

