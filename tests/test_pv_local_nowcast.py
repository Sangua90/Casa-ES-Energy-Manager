"""Local PV validation, bounded optimism and actual-power floor."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pv_local", ROOT / "custom_components/casa_es_energy_manager/pv_local_nowcast.py")
pv = importlib.util.module_from_spec(spec); spec.loader.exec_module(pv)
NOW = datetime(2026,10,12,12,tzinfo=timezone(timedelta(hours=2)))
ROLES = {k:k for k in ("irradiance","actual","soc","forecast")}

class ModelTests(unittest.TestCase):
    def statistics(self, forecast_scale=.6, soc=50):
        data = {k:[] for k in ROLES}
        for day in range(7):
            for hour in (10,11,12):
                for minute in range(0,60,5):
                    at = (NOW-timedelta(days=day+1)).replace(hour=hour,minute=minute)
                    ghi=300+minute
                    for key,value in (("irradiance",ghi),("actual",ghi*10),("soc",soc),("forecast",ghi*10*forecast_scale)):
                        data[key].append({"start":at.timestamp()*1000,"mean":value})
        return data

    def test_accepts_only_independently_better_model(self):
        model=pv.fit(self.statistics(),ROLES,NOW,8720)
        self.assertTrue(model["accepted"])
        self.assertEqual(model["validation_mae_w"],0)
        self.assertEqual(model["validation_samples"],72)
        self.assertFalse(pv.fit(self.statistics(forecast_scale=1),ROLES,NOW,8720)["accepted"])
        self.assertFalse(pv.fit(self.statistics(soc=99),ROLES,NOW,8720)["accepted"])

    def test_optimistic_margin_is_small_and_bounded(self):
        model=pv.fit(self.statistics(),ROLES,NOW,8720)
        self.assertEqual(pv.estimate(model,300,NOW,8720),3090)
        self.assertEqual(pv.estimate(model,300,NOW,8720,10,50),3050)
        self.assertIsNone(pv.estimate(model,300,NOW+timedelta(days=15),8720))
        self.assertIsNone(pv.estimate(model,300,NOW,9000))
        self.assertIsNone(pv.estimate(model,1000,NOW,8720))
        self.assertEqual(pv.estimate(model,0,NOW,8720),0)

    def test_actual_floor_and_missing_inputs(self):
        self.assertEqual(pv.combined(6000,4000),6000)
        self.assertEqual(pv.combined(4000,6000),6000)
        self.assertEqual(pv.combined(4000,None),4000)
        self.assertIsNone(pv.combined(float("nan"),None))


import ast
from types import SimpleNamespace

class Base:
    def _numeric_state(self, entity_id, **kwargs):
        return self.values.get(entity_id)

source=(ROOT / "custom_components/casa_es_energy_manager/coordinator_v1522.py").read_text(encoding="utf-8")
body=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef)).body
methods=[n for n in body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ("_numeric_state","_refresh_pv_nowcast")]
module=ast.Module(body=[ast.ClassDef(name="Adapter",bases=[ast.Name(id="Base",ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
namespace={"Base":Base,"finite":lambda value,default=0: pv.number(value) if pv.number(value) is not None else default,
           "estimate_pv":pv.estimate,"combine_pv":pv.combined}
exec(compile(ast.fix_missing_locations(module),"pv_adapter","exec"),namespace)
Adapter=namespace["Adapter"]

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setup_adapter(self):
        c=Adapter();c._pv_capacity=lambda:8720;c._pv_next_learning=NOW.timestamp()+86400
        c._pv_model=pv.fit(ModelTests().statistics(),ROLES,NOW,8720);c._pv_info={}
        c._config=lambda key:{"pv_power_sensor":"actual","battery_soc_sensor":"soc","pv_potential_power_sensor":"fv_pro"}.get(key)
        c.values={"actual":6000,"sensor.casa_es_power_production_now":4500,"fv_pro":4500}
        states={"input_boolean.energy_manager_stima_fv_locale":SimpleNamespace(state="on"),"sensor.stazione_meteo_solar_radiation":SimpleNamespace(state="300",last_updated=NOW,attributes={"unit_of_measurement":"W/m²"})}
        c.hass=SimpleNamespace(states=SimpleNamespace(get=states.get))
        return c,states

    async def test_control_reads_same_max_policy_as_display(self):
        c,_=self.setup_adapter();await c._refresh_pv_nowcast(NOW)
        self.assertEqual(c._pv_estimate,3090)
        self.assertEqual(c._numeric_state("fv_pro",power=True),6000)
        self.assertEqual(c._numeric_state("actual",power=True),6000)
        self.assertEqual(c._pv_info["method"],"local_irradiance_validated")

    async def test_stale_weather_returns_to_meteo_with_real_floor(self):
        c,states=self.setup_adapter();states["sensor.stazione_meteo_solar_radiation"].last_updated=NOW-timedelta(minutes=6)
        await c._refresh_pv_nowcast(NOW)
        self.assertEqual(c._pv_estimate,4500)
        self.assertEqual(c._numeric_state("fv_pro",power=True),6000)
        self.assertEqual(c._pv_info["method"],"weather_forecast_fallback")

    async def test_local_model_can_be_disabled_without_losing_real_floor(self):
        c,states=self.setup_adapter();states["input_boolean.energy_manager_stima_fv_locale"].state="off"
        await c._refresh_pv_nowcast(NOW)
        self.assertEqual(c._pv_estimate,4500)
        self.assertEqual(c._numeric_state("fv_pro",power=True),6000)
        self.assertFalse(c._pv_info["local_model_enabled"])
