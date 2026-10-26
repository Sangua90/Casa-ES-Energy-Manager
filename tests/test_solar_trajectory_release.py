"""Measured PV remaining after battery trajectory reaches both thermal paths."""
import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from test_dhw_history_plan import dhw
ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'custom_components/casa_es_energy_manager/coordinator_v1520.py').read_text(encoding='utf-8')
body=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='CasaESEnergyCoordinator').body
methods=[n for n in body if isinstance(n,ast.FunctionDef) and n.name in ('_virtual_surplus_opportunity','_thermal_solar_available')]
class Previous:
    def _virtual_surplus_opportunity(self,data): return bool(data.get('old_probe'))
    def _battery_allocation(self,data): return {'below_target':False,'reserved_w':0}
module=ast.Module(body=[ast.ClassDef(name='Controller',bases=[ast.Name(id='Previous',ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
ns={'Previous':Previous,'number':dhw.number,'Any':object}
exec(compile(ast.fix_missing_locations(module),'solar_release','exec'),ns)
class SolarTests(unittest.TestCase):
    def data(self):
        return {'battery_soc':90,'solar_after_house_w':3391,'battery_discharge_w':0,'grid_import_w':0,
                'planner_policy':{'target_reachability':'comfortable'},
                'v156_battery_allocation':{'reserved_w':891.4,'hard_recovery':False,'below_target':True,'overflow_w':2499.6}}
    def test_measured_overflow_released_before_battery_full(self):
        c=ns['Controller'](); self.assertTrue(c._virtual_surplus_opportunity(self.data()))
    def test_shared_budget_caps_optimism_and_reserves_battery(self):
        d=self.data();d['pv_potential_after_house_w']=8000
        self.assertAlmostEqual(ns['Controller']()._thermal_solar_available(d),2499.6)

    def test_forecast_alone_cannot_create_safe_overflow(self):
        d=self.data(); d.update(solar_after_house_w=800,pv_potential_after_house_w=5000)
        self.assertFalse(ns['Controller']()._virtual_surplus_opportunity(d))
    def test_recovery_import_discharge_and_missing_telemetry_block(self):
        for key,value in [('battery_discharge_w',101),('grid_import_w',101),('solar_after_house_w',None),('grid_warning',True),('inverter_warning',True)]:
            with self.subTest(key=key):
                d=self.data();d[key]=value
                self.assertFalse(ns['Controller']()._virtual_surplus_opportunity(d))
        d=self.data();d['v156_battery_allocation']['hard_recovery']=True
        self.assertFalse(ns['Controller']()._virtual_surplus_opportunity(d))
        d=self.data();d['planner_policy']['target_reachability']='definite_shortfall'
        self.assertFalse(ns['Controller']()._virtual_surplus_opportunity(d))
    def test_existing_guarded_clipping_probe_preserved(self):
        self.assertTrue(ns['Controller']()._virtual_surplus_opportunity({'old_probe':True}))
