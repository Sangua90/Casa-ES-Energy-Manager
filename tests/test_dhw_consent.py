"""Consent is explicit, single-use, bounded and persistent across restart."""
from datetime import timedelta
import unittest

from test_dhw_control import consent_module
from test_dhw_history_plan import dhw, NOW


class ConsentTests(unittest.TestCase):
    def setup_consent(self):
        c = consent_module.DHWRecoveryConsent()
        p = dhw.plan({}, NOW, 40, 53, 73)
        r = c.request("boiler", p, NOW)
        return c, p, r

    def test_no_response_and_unrelated_action_never_approve(self):
        c, _, _ = self.setup_consent()
        self.assertIsNone(c.approved_target("boiler", NOW))
        self.assertIsNone(c.answer("YES", NOW))
        self.assertIsNone(c.approved_target("boiler", NOW))

    def test_no_deduplicates_same_day_after_restart(self):
        c, p, r = self.setup_consent()
        c.answer("CASA_ES_DHW_NO_" + r["token"], NOW)
        restarted = consent_module.DHWRecoveryConsent(c.records)
        self.assertIsNone(restarted.request("boiler", p, NOW))
        self.assertIsNone(restarted.approved_target("boiler", NOW))

    def test_yes_is_bounded_and_cannot_be_replayed_after_finish(self):
        c, _, r = self.setup_consent()
        action = "CASA_ES_DHW_YES_" + r["token"]
        self.assertEqual(c.answer(action, NOW), "boiler")
        self.assertEqual(c.approved_target("boiler", NOW), r["target_c"])
        self.assertIsNone(c.approved_target("boiler", NOW + timedelta(hours=25)))
        c.finish("boiler")
        self.assertIsNone(c.answer(action, NOW))
        self.assertIsNone(c.approved_target("boiler", NOW))

    def test_late_answer_cannot_activate(self):
        c, _, r = self.setup_consent()
        self.assertIsNone(c.answer("CASA_ES_DHW_YES_" + r["token"], NOW + timedelta(minutes=91)))
        self.assertIsNone(c.approved_target("boiler", NOW + timedelta(minutes=91)))

    def test_colder_tank_and_slower_resistance_notify_earlier(self):
        now = NOW.replace(hour=10)
        warm = dhw.plan({}, now, 50, 53, 73)
        cold = dhw.plan({}, now, 30, 53, 73)
        slow = dhw.plan({"boost_c_per_h": 2}, now, 30, 53, 73)
        self.assertGreater(cold["recovery_request_lead_hours"], warm["recovery_request_lead_hours"])
        self.assertGreater(slow["recovery_request_lead_hours"], cold["recovery_request_lead_hours"])
        self.assertAlmostEqual(cold["recovery_request_lead_hours"] - cold["boost_heating_hours"], 0.75, places=2)
        self.assertGreater(cold["boost_heating_hours"], 2)


    def test_expired_morning_request_allows_evening_but_not_repeat(self):
        c, p, r = self.setup_consent()
        r.update(status="expired", deadline=NOW.replace(hour=7).isoformat())
        p["deadline"] = NOW.replace(hour=19).isoformat()
        self.assertIsNotNone(c.request("boiler", p, NOW.replace(hour=16)))
        c.records["boiler"]["status"] = "expired"
        self.assertIsNone(c.request("boiler", p, NOW.replace(hour=17)))

    def test_previous_evening_approval_covers_morning_without_renewal(self):
        c, p, _ = self.setup_consent()
        c.records.clear()
        now = NOW.replace(hour=21)
        p["deadline"] = (now + timedelta(days=1)).replace(hour=7).isoformat()
        r = c.request("boiler", p, now)
        c.answer("CASA_ES_DHW_YES_" + r["token"], now)
        self.assertIsNotNone(c.approved_target("boiler", (now + timedelta(days=1)).replace(hour=7)))
        self.assertIsNone(c.approved_target("boiler", now + timedelta(hours=25)))
