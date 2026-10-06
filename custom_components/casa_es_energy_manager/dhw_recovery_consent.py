"""One-use, expiring permission for a grid-assisted hot-water recovery."""
from datetime import datetime, timedelta
from uuid import uuid4


class DHWRecoveryConsent:
    def __init__(self, records=None):
        self.records = records if isinstance(records, dict) else {}

    def request(self, sid, plan, now):
        episode = datetime.fromisoformat(plan["deadline"]).date().isoformat()
        previous = self.records.get(sid, {})
        if previous.get("episode") == episode and not (
            previous.get("status") == "notification_failed" and
            now.timestamp() >= datetime.fromisoformat(previous["retry_at"]).timestamp()
        ):
            return None
        token = uuid4().hex
        record = {"episode": episode, "status": "pending", "token": token,
                  "target_c": plan["target_c"],
                  "runtime_hours": min(max(plan["boost_heating_hours"], 2), 24),
                  "expires": (now + timedelta(minutes=45)).isoformat()}
        self.records[sid] = record
        return record

    def answer(self, action, now):
        for sid, record in self.records.items():
            if record.get("status") != "pending":
                continue
            token = record.get("token")
            if action not in (f"CASA_ES_DHW_YES_{token}", f"CASA_ES_DHW_NO_{token}"):
                continue
            if now.timestamp() >= datetime.fromisoformat(record["expires"]).timestamp():
                record["status"] = "expired"
                return None
            yes = action == f"CASA_ES_DHW_YES_{token}"
            record["status"] = "approved" if yes else "declined"
            if yes:
                record["expires"] = (now + timedelta(hours=record["runtime_hours"])).isoformat()
            return sid
        return None

    def approved_target(self, sid, now):
        record = self.records.get(sid, {})
        if record.get("status") != "approved":
            return None
        if now.timestamp() >= datetime.fromisoformat(record["expires"]).timestamp():
            return None
        return record["target_c"]

    def finish(self, sid):
        record = self.records.get(sid, {})
        if record.get("status") == "approved":
            record["status"] = "completed"
        record.pop("owned", None)
