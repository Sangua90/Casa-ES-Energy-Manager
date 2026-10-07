"""Shared finite-horizon thermal dispatch, 24 hours at 15-minute resolution."""
from datetime import timedelta
from house_climate_plan import finite, target_level, heat_costs
from thermal_model import predicted_rate


def optimize(house, zones, models, now, energy):
    temperatures = {z["id"]: finite(z.get("temperature")) for z in zones}
    budget = max(finite(energy.get("thermal_budget_kwh"), 0), 0)
    rows, machine_kwh = [], {}
    weather = []
    from datetime import datetime
    for point in energy.get("outdoor_forecast", []):
        try:
            stamp = datetime.fromisoformat(point["datetime"].replace("Z", "+00:00"))
            temp = finite(point.get("temperature"))
            if stamp.tzinfo and temp is not None and -40 <= temp <= 50:
                weather.append((stamp.timestamp(), temp))
        except (ValueError, KeyError, TypeError):
            continue
    weather.sort()
    def outdoor_at(zone, at):
        available = [temp for stamp, temp in weather if stamp <= at.timestamp()]
        return available[-1] if available else finite(zone.get("outdoor_temperature"))
    for step in range(96):
        at = now + timedelta(minutes=15 * step)
        candidates, targets = [], {}
        for zone in zones:
            key, room = zone["id"], zone["config"]
            current = temperatures[key]
            target, level, occupied, preparing = target_level(house, room, at)
            targets[key] = (target, level)
            if current is None:
                continue
            model = models.get(key, {})
            outdoor = outdoor_at(zone, at)
            drifting = current + predicted_rate(model, "off", current, outdoor) * .25
            deficit = max(target - drifting, 0)
            cooling = house.get("season") == "summer"
            if cooling:
                deficit = max(drifting - finite(room.get("cooling_temperature"), 26), 0) if occupied else 0
            if deficit < .25 or not room.get("reviewed"):
                continue
            priority = finite(room.get("priority"), 2) * deficit * (2 if occupied and not preparing else 1)
            cost = heat_costs(house, room)
            source = "off"
            # Budget is only certified overflow. Purchased energy requires
            # independently confirmed marginal prices and COP.
            if house.get("machine_modes", {}).get(room.get("machine"), "auto") == "auto" and room.get("heat_pump_entity") and (budget > 0 or house.get("allow_economic_grid") and cost["economics_verified"] and cost["heat_pump_eur_kwh_heat"] < cost["gas_eur_kwh_heat"] * .95):
                source = "cooling" if cooling else "heat_pump"
            elif house.get("season") == "winter" and house.get("hydraulics_confirmed") and house.get("gas_mode", "auto") == "auto" and room.get("radiator_entities"):
                source = "gas"
            if source != "off":
                candidates.append((priority, key, zone, source))
        dispatch, watts = {}, 0
        groups = {}
        for _, key, zone, source in sorted(candidates, key=lambda x: (-x[0], x[1])):
            room = zone["config"]
            group = room.get("machine", key)
            power = finite(room.get("nominal_power_w"), 1200) if source != "gas" else 0
            # Shared outdoor unit: reserve group maximum, never sum each head's
            # duplicated group telemetry. Live guards still use conservative
            # full-power headroom for each new head until power is verified.
            increment = max(power - groups.get(group, 0), 0)
            if watts + increment > finite(energy.get("hvac_power_limit_w"), 3000):
                continue
            needed = increment * .25 / 1000
            if source != "gas" and needed > budget and not house.get("allow_economic_grid"):
                continue
            watts += increment
            groups[group] = max(groups.get(group, 0), power)
            budget = max(budget - needed, 0)
            machine_kwh[group] = machine_kwh.get(group, 0) + needed
            dispatch[key] = source
        for zone in zones:
            key = zone["id"]
            current = temperatures[key]
            if current is not None:
                source = dispatch.get(key, "off")
                rate = predicted_rate(models.get(key, {}), source, current, outdoor_at(zone, at))
                temperatures[key] = max(5, min(current + rate * .25, 35))
        if dispatch or step % 4 == 0:
            rows.append({"timestamp": at.isoformat(), "sources": dispatch,
                         "temperatures": {k: round(v, 2) if v is not None else None for k, v in temperatures.items()},
                         "targets": {k: {"temperature": t, "level": l} for k, (t, l) in targets.items()},
                         "reserved_power_w": watts, "remaining_thermal_budget_kwh": round(budget, 3)})
    return {"steps": rows, "machine_energy_kwh": machine_kwh,
            "assumptions": ["weather_forecast" if weather else "current_outdoor_until_weather_forecast", "configured_cop_until_confirmed", "unknown_future_solar_not_spent"],
            "algorithm": "priority_shared_budget_receding_horizon"}
