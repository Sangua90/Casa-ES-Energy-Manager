"""Versioned numerical planning API. It has no access to device services."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from statistics import median
import json
import os

from house_climate_plan import finite, room_plan, next_deadline
from thermal_model import estimate, fit_context, predicted_rate
from horizon import optimize

SCHEMA = 1


class Engine:
    def __init__(self, path):
        self.path = Path(path)
        self.models, self.previous, self.decisions = {}, {}, []
        self.saved_at = 0
        self.bootstrap_until = 0
        self.machine_models = {}
        self.gas_campaign = {}
        self.cooldowns = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("schema") != SCHEMA:
                raise ValueError("Unsupported persisted schema")
            self.models = saved.get("models", {})
            self.bootstrap_until = saved.get("bootstrap_until", 0)
            self.machine_models = saved.get("machine_models", {})
            self.gas_campaign = saved.get("gas_campaign", {})
            self.decisions = saved.get("decisions", [])[-100:]

    def save(self, now):
        if now - self.saved_at < 300:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema": SCHEMA, "models": self.models,
                                         "decisions": self.decisions[-100:], "bootstrap_until": self.bootstrap_until,
                                         "machine_models": self.machine_models, "gas_campaign": self.gas_campaign}))
        os.replace(temporary, self.path)
        self.saved_at = now

    def learn(self, zone, now):
        key = zone["id"]
        current = finite(zone.get("temperature"))
        previous = self.previous.get(key)
        signature = zone.get("thermal_source", "unknown")
        root = self.models.get(key, {})
        if not zone.get("contamination") and zone.get("sensors"):
            self.models[key] = root
            current, quality = estimate({**zone, "timestamp": now}, root)
            root["temperature_estimate"] = {"value": current, "quality": quality, "updated_at": now}
        clean = not zone.get("contamination") and current is not None
        if previous and current is not None and previous.get("temperature") is not None and 0 < now - previous["at"] <= 900 and abs(current - previous["temperature"]) > 2:
            clean = False
        if clean and previous and previous.get("clean") and previous["source"] in ("gas", "heat_pump", "combined") and signature == "off":
            self.cooldowns[key] = {"at": now, "start": current, "peak": current, "source": previous["source"]}
        cooldown = self.cooldowns.get(key)
        if cooldown and clean and signature == "off":
            cooldown["peak"] = max(cooldown["peak"], current)
            if now - cooldown["at"] >= 3600 or current < cooldown["peak"] - .2:
                root = self.models.setdefault(key, {})
                inertia = root.setdefault("inertia", {"overshoots": [], "settling_minutes": []})
                inertia["overshoots"] = (inertia["overshoots"] + [min(max(cooldown["peak"] - cooldown["start"], 0), 2)])[-40:]
                inertia["settling_minutes"] = (inertia["settling_minutes"] + [(now - cooldown["at"]) / 60])[-40:]
                inertia.update(overshoot_c=median(inertia["overshoots"]), samples=len(inertia["overshoots"]), updated_at=now)
                self.cooldowns.pop(key, None)
        elif cooldown and (not clean or signature != "off"):
            self.cooldowns.pop(key, None)
        if clean and previous and previous["clean"] and signature == previous["source"]:
            seconds = now - previous["at"]
            change = current - previous["temperature"]
            # Short, quantized samples are not thermal-rate observations.
            if 900 <= seconds <= 7200 and .15 <= abs(change) <= 4:
                rate = change * 3600 / seconds
                if abs(rate) <= 5 and signature in ("off", "gas", "heat_pump", "combined", "cooling"):
                    model = self.models.setdefault(key, {}).setdefault(signature, {"rates": []})
                    rates = model["rates"]
                    if len(rates) < 10 or abs(rate - median(rates)) < max(1, abs(median(rates)) * 2):
                        rates.append(rate)
                        del rates[:-96]
                        root = self.models.setdefault(key, {})
                        fit_context(root, {"rate": rate, "source": signature, "temperature": current,
                                             "outdoor": finite(zone.get("outdoor_temperature")), "solar_w": finite(zone.get("solar_power_w")), "at": now,
                                             "neighbor_gradients": zone.get("neighbor_gradients", {})})
                        model.update(rate_c_h=median(rates), samples=len(rates),
                                     confidence=min(len(rates) / 40, .9), updated_at=now)
            elif seconds < 900 or (seconds < 7200 and abs(change) < .15):
                return
        self.previous[key] = {"at": now, "temperature": current, "source": signature, "clean": clean}

    def plan(self, payload):
        if payload.get("schema") != SCHEMA:
            raise ValueError("Unsupported protocol schema")
        now = datetime.fromisoformat(payload["timestamp"])
        if now.tzinfo is None:
            raise ValueError("Timestamp must include timezone")
        house = payload["house"]
        if house.get("timezone"):
            now = now.astimezone(ZoneInfo(house["timezone"]))
        zones = payload.get("zones", [])
        if not isinstance(zones, list) or len(zones) > 100:
            raise ValueError("Invalid zone list")
        results, reasons = {}, []
        history = payload.get("history", [])
        if not isinstance(history, list) or len(history) > 3000:
            raise ValueError("Invalid history")
        for point in sorted(history, key=lambda p: p["at"]):
            stamp = finite(point.get("at"))
            if stamp is None or stamp <= self.bootstrap_until or stamp >= now.timestamp():
                continue
            for historical_zone in point.get("zones", []):
                self.learn(historical_zone, stamp)
            self.bootstrap_until = stamp
        for zone in zones:
            root = self.models.get(zone["id"], {})
            if zone.get("sensors"):
                value, quality = estimate({**zone, "timestamp": now.timestamp()}, root)
                zone["temperature"] = value
                zone["estimate_quality"] = quality
        for zone in zones:
            self.learn(zone, now.timestamp())
            room = dict(zone["config"])
            model = self.models.get(zone["id"], {})
            heating = [m["rate_c_h"] for source, m in model.items()
                       if source in ("gas", "heat_pump", "combined") and m.get("samples", 0) >= 10 and m.get("rate_c_h", 0) > .1]
            current = finite(zone.get("temperature"))
            deadline = next_deadline(room, now)
            if current is not None:
                eligible = [m["rate_c_h"] for source, m in model.items() if source in (("heat_pump",) if not house.get("hydraulics_confirmed") else ("gas", "heat_pump", "combined")) and m.get("samples", 0) >= 10 and m.get("rate_c_h", 0) > .1]
                conservative = min(eligible) if eligible else .7
                cooling_rate = predicted_rate(model, "off", current, finite(zone.get("outdoor_temperature")), zone.get("neighbor_gradients"), zone.get("solar_power_w"))
                hours_to_use = max((deadline.timestamp() - now.timestamp()) / 3600, 0) if deadline else 0
                predicted = current + min(cooling_rate, 0) * min(hours_to_use, 12)
                deficit = max(finite(room.get("comfort_temperature"), 21) - predicted, 0)
                minutes = deficit / conservative * 60
                room["preheat_minutes"] = min(max(minutes * 1.25 + 15, 15), 480)
            decision = room_plan(house, room, now, current, bool(zone.get("solar_available")), bool(zone.get("electrical_ok")))
            inertia = model.get("inertia", {})
            decision.update(overshoot_c=inertia.get("overshoot_c", 0) if inertia.get("samples", 0) >= 5 else 0,
                            model=model, preheat_minutes=room.get("preheat_minutes", 90),
                            confidence=max((m.get("confidence", 0) for k, m in model.items() if k in ("off", "gas", "heat_pump", "combined")), default=0),
                            estimated_temperature=current, estimate_quality=zone.get("estimate_quality", "fallback"),
                            deadline=deadline.isoformat() if deadline else None, learning_status="conservative_fallback" if not heating else "thermal_rate_available")
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
        thermal_budget = max(remaining - battery_need - finite(energy.get("house_remaining_kwh"), 0), 0) if remaining is not None and battery_need is not None else 0
        horizon = optimize(house, zones, self.models, now, {**energy, "thermal_budget_kwh": thermal_budget})
        machines = {}
        for zone in zones:
            group = zone["config"].get("machine", "none")
            if group == "none":
                continue
            machine = machines.setdefault(group, {"active_heads": 0, "power_w": 0})
            machine["active_heads"] += zone.get("thermal_source") in ("heat_pump", "combined", "cooling")
            machine["power_w"] = max(machine["power_w"], finite(zone.get("power_w"), 0))
        for group, sample in machines.items():
            if sample["active_heads"] and sample["power_w"] > 0:
                bucket = self.machine_models.setdefault(group, {}).setdefault(str(sample["active_heads"]), {"powers": []})
                bucket["powers"] = (bucket["powers"] + [sample["power_w"]])[-96:]
                bucket.update(power_w=median(bucket["powers"]), samples=len(bucket["powers"]), updated_at=now.timestamp())
        gas_meter = finite(payload.get("gas_meter"))
        if gas_meter is not None:
            previous = self.gas_campaign.get("reading")
            if previous is not None and gas_meter > previous:
                self.gas_campaign["last_delta_m3"] = gas_meter - previous
            if previous != gas_meter:
                self.gas_campaign.update(reading=gas_meter, at=now.isoformat(), attribution="whole_house_including_acs")
        for key, result in results.items():
            event = {"zone": key, "source": result["source"], "target": result["heat_pump_target"],
                     "radiator_target": result["radiator_target"], "confidence": result["confidence"]}
            last = next((e for e in reversed(self.decisions) if e["zone"] == key), None)
            if not last or any(last.get(k) != event[k] for k in ("source", "target", "radiator_target")):
                self.decisions.append({**event, "timestamp": now.isoformat()})
        self.decisions = self.decisions[-100:]
        self.save(now.timestamp())
        return {"schema": SCHEMA, "timestamp": now.isoformat(),
                "expires_at": datetime.fromtimestamp(now.timestamp() + 120, now.tzinfo).isoformat(),
                "state": "DEGRADED" if reasons else "NORMAL", "reasons": reasons, "zones": results,
                "energy_budget": {"battery_need_kwh": battery_need, "pv_remaining_kwh": remaining,
                                  "unallocated_kwh": max(remaining - battery_need - finite(energy.get("house_remaining_kwh"), 0), 0)
                                  if remaining is not None and battery_need is not None else None},
                "recent_decisions": self.decisions[-20:], "horizon": horizon,
                "history_imported_until": self.bootstrap_until, "machine_models": self.machine_models, "gas_campaign": self.gas_campaign}

    def import_history(self, payload):
        if payload.get("schema") != SCHEMA or not isinstance(payload.get("points"), list) or len(payload["points"]) > 96:
            raise ValueError("Invalid history batch")
        imported = 0
        # Replay uses a separate predecessor stream so a retry/import cannot
        # corrupt live learning. Checkpoint only after a whole timestamp.
        live_previous = self.previous
        self.previous = getattr(self, "historical_previous", {})
        try:
            for point in sorted(payload["points"], key=lambda p: p["at"]):
                stamp = finite(point.get("at"))
                if stamp is None or stamp <= self.bootstrap_until:
                    continue
                for zone in point.get("zones", []):
                    self.learn(zone, stamp)
                self.bootstrap_until = stamp
                imported += 1
            self.historical_previous = self.previous
        finally:
            self.previous = live_previous
        self.saved_at = 0
        self.save(datetime.now().timestamp())
        return {"schema": SCHEMA, "accepted": True, "imported": imported, "until": self.bootstrap_until}
