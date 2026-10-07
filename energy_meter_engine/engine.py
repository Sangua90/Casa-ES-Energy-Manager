"""Versioned numerical planning API. It has no access to device services."""
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
import json
import os

from house_climate_plan import finite, room_plan

SCHEMA = 1


class Engine:
    def __init__(self, path):
        self.path = Path(path)
        self.models, self.previous, self.decisions = {}, {}, []
        self.saved_at = 0
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved.get("schema") != SCHEMA:
                raise ValueError("Unsupported persisted schema")
            self.models = saved.get("models", {})
            self.decisions = saved.get("decisions", [])[-100:]

    def save(self, now):
        if now - self.saved_at < 300:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema": SCHEMA, "models": self.models,
                                         "decisions": self.decisions[-100:]}))
        os.replace(temporary, self.path)
        self.saved_at = now

    def learn(self, zone, now):
        key = zone["id"]
        current = finite(zone.get("temperature"))
        previous = self.previous.get(key)
        signature = zone.get("thermal_source", "unknown")
        clean = not zone.get("contamination") and current is not None
        if clean and previous and previous["clean"] and signature == previous["source"]:
            seconds = now - previous["at"]
            change = current - previous["temperature"]
            # Short, quantized samples are not thermal-rate observations.
            if 900 <= seconds <= 7200 and .15 <= abs(change) <= 4:
                rate = change * 3600 / seconds
                if abs(rate) <= 5 and signature in ("off", "gas", "heat_pump", "combined"):
                    model = self.models.setdefault(key, {}).setdefault(signature, {"rates": []})
                    rates = model["rates"]
                    if len(rates) < 10 or abs(rate - median(rates)) < max(1, abs(median(rates)) * 2):
                        rates.append(rate)
                        del rates[:-96]
                        model.update(rate_c_h=median(rates), samples=len(rates),
                                     confidence=min(len(rates) / 40, .9), updated_at=now)
            elif seconds < 900:
                return
        self.previous[key] = {"at": now, "temperature": current, "source": signature, "clean": clean}

    def plan(self, payload):
        if payload.get("schema") != SCHEMA:
            raise ValueError("Unsupported protocol schema")
        now = datetime.fromisoformat(payload["timestamp"])
        if now.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        house = payload["house"]
        zones = payload.get("zones", [])
        if not isinstance(zones, list) or len(zones) > 100:
            raise ValueError("Invalid zone list")
        results, reasons = {}, []
        for zone in zones:
            self.learn(zone, now.timestamp())
            room = dict(zone["config"])
            model = self.models.get(zone["id"], {})
            heating = [m["rate_c_h"] for source, m in model.items()
                       if source != "off" and m.get("samples", 0) >= 10 and m.get("rate_c_h", 0) > .1]
            current = finite(zone.get("temperature"))
            if current is not None and heating:
                minutes = max((finite(room.get("comfort_temperature"), 21) - current) / max(heating) * 60, 0)
                room["preheat_minutes"] = min(max(minutes * 1.25 + 15, 15), 240)
            decision = room_plan(house, room, now, current, bool(zone.get("solar_available")), bool(zone.get("electrical_ok")))
            decision.update(model=model, preheat_minutes=room.get("preheat_minutes", 90),
                            confidence=max((m.get("confidence", 0) for m in model.values()), default=0),
                            learning_status="observing" if not heating else "thermal_rate_available")
            results[zone["id"]] = decision
            if current is None:
                reasons.append("temperature_unavailable:" + zone["id"])
        energy = payload.get("energy", {})
        remaining = finite(energy.get("pv_remaining_kwh"))
        capacity = finite(energy.get("battery_capacity_kwh"))
        soc, target = finite(energy.get("soc")), finite(energy.get("target_soc"))
        battery_need = (max(target - soc, 0) * capacity / 100
                        if capacity and soc is not None and target is not None else None)
        # Prefer the integration's verified, same-horizon calculation, which
        # already includes configured charge efficiency and dynamic SOC target.
        battery_input_need = finite(energy.get("battery_input_need_kwh"))
        if battery_input_need is not None and battery_input_need >= 0:
            battery_need = battery_input_need
        if remaining is None:
            reasons.append("forecast_unavailable")
        if battery_need is None:
            reasons.append("battery_budget_unavailable")
        if not house.get("reviewed"):
            reasons.append("configuration_pending_review")
        for key, result in results.items():
            event = {"zone": key, "source": result["source"], "target": result["heat_pump_target"],
                     "radiator_target": result["radiator_target"], "confidence": result["confidence"]}
            last = next((e for e in reversed(self.decisions) if e["zone"] == key), None)
            if not last or any(last.get(k) != event[k] for k in ("source", "target", "radiator_target")):
                self.decisions.append({**event, "timestamp": now.isoformat()})
        self.decisions = self.decisions[-100:]
        self.save(now.timestamp())
        return {"schema": SCHEMA, "timestamp": now.isoformat(),
                "expires_at": (now + timedelta(seconds=120)).isoformat(),
                "state": "DEGRADED" if reasons else "NORMAL", "reasons": reasons, "zones": results,
                "energy_budget": {"battery_need_kwh": battery_need, "pv_remaining_kwh": remaining,
                                  "unallocated_kwh": max(remaining - battery_need - finite(energy.get("house_remaining_kwh"), 0), 0)
                                  if remaining is not None and battery_need is not None else None},
                "recent_decisions": self.decisions[-20:]}

