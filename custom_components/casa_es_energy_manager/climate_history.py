"""Bounded, attribute-preserving Recorder replay on an absolute time grid."""
from bisect import bisect_right
from datetime import datetime
from .house_climate_plan import finite, celsius, auxiliary_active


def epoch(value):
    if isinstance(value, datetime):
        return value.timestamp() if value.tzinfo else None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.timestamp() if dt.tzinfo else None
    except ValueError:
        return None




class Replay:
    def __init__(self, rows):
        self.rows, self.times = {}, {}
        for entity, values in rows.items():
            pairs = sorted(((t, row) for row in values if (t := epoch(row.get("last_updated"))) is not None), key=lambda pair: pair[0])
            self.rows[entity] = [row for _, row in pairs]
            self.times[entity] = [t for t, _ in pairs]

    def state(self, entity, at):
        index = bisect_right(self.times.get(entity, []), at) - 1
        return self.rows[entity][index] if index >= 0 else None

    def temperature(self, entity, at, unit, minimum=-10):
        row = self.state(entity, at)
        if not row or row.get("state") in ("unknown", "unavailable"):
            return None
        attrs = row.get("attributes", {})
        return celsius(attrs.get("current_temperature"), unit) if entity.startswith("climate.") else celsius(row.get("state"), attrs.get("unit_of_measurement"), minimum)


def replay(rows, rooms, house, start, end, unit="°C"):
    """15-minute points, max 30 days; held states are valid until next change.

    Missing source states never become 'off'. Attribute-only heating/power
    transitions are included. No inference of routines from manual activity.
    """
    history = Replay(rows)
    begin, finish = epoch(start), epoch(end)
    if begin is None or finish is None or finish <= begin or finish - begin > 31 * 86400:
        raise ValueError("Invalid aware history interval")
    points = []
    for at in range(int(begin), int(finish) + 1, 900):
        zones = []
        for key, room in rooms.items():
            sensors = {}
            for entity in [room.get("temperature_entity", ""), *(room.get("radiator_entities") or []), room.get("heat_pump_entity", "")]:
                if entity and (temp := history.temperature(entity, at, unit)) is not None:
                    sensors[entity] = temp
            independent = sensors.get(room.get("temperature_entity"))
            valves = [sensors[e] for e in room.get("radiator_entities", []) if e in sensors]
            temperature = independent if independent is not None else sum(valves) / len(valves) if valves else sensors.get(room.get("heat_pump_entity"))
            hp = history.state(room.get("heat_pump_entity", ""), at)
            gas = history.state(house.get("gas_entity", ""), at)
            flags = []
            if room.get("heat_pump_entity") and (not hp or hp.get("state") in ("unknown", "unavailable")):
                flags.append("heat_pump_state_missing")
            if room.get("radiator_entities") and (not gas or gas.get("state") in ("unknown", "unavailable")):
                flags.append("gas_state_missing")
            for entity in (room.get("window_entities") or [room.get("window_entity", "")]):
                if entity and (not (w := history.state(entity, at)) or w.get("state") != "off"):
                    flags.append("window_open_or_unknown")
            for entity in room.get("contamination_entities", []):
                event = history.state(entity, at)
                if not event or auxiliary_active(event.get("state"), event.get("attributes", {})):
                    flags.append("auxiliary_source_or_unknown")
            power_row = history.state(room.get("power_entity", ""), at)
            power = finite((power_row or {}).get("state"))
            power_unit = (power_row or {}).get("attributes", {}).get("unit_of_measurement")
            power = max(power * (1000 if power_unit == "kW" else 1), 0) if power is not None and power_unit in ("W", "kW") else None
            hp_active = bool(hp and hp.get("state") in ("heat", "cool") and (hp.get("attributes", {}).get("hvac_action") in ("heating", "cooling") or finite(power, 0) > 100 or finite(hp.get("attributes", {}).get("compressor_frequency"), 0) > 0))
            valve_heating = any((v := history.state(e, at)) and v.get("attributes", {}).get("hvac_action") == "heating" for e in room.get("radiator_entities", []))
            gas_active = bool(gas and gas.get("attributes", {}).get("hvac_action") == "heating" and valve_heating)
            source = "combined" if hp_active and gas_active else "cooling" if hp_active and hp["state"] == "cool" else "heat_pump" if hp_active else "gas" if gas_active else "off"
            pv = history.state(house.get("pv_entity", ""), at)
            solar_power = finite((pv or {}).get("state"))
            pv_unit = (pv or {}).get("attributes", {}).get("unit_of_measurement")
            solar_power = solar_power * (1000 if pv_unit == "kW" else 1) if solar_power is not None and pv_unit in ("W", "kW") else None
            neighbors = {e: value - temperature for e in room.get("neighbor_temperature_entities", []) if temperature is not None and (value := history.temperature(e, at, unit)) is not None}
            zones.append({"id": key, "config": room, "temperature": temperature, "sensors": sensors,
                          "independent_temperature": independent, "thermal_source": source,
                          "contamination": flags, "power_w": power,
                          "solar_power_w": solar_power, "neighbor_gradients": neighbors, "outdoor_temperature": history.temperature(house.get("outdoor_entity", ""), at, unit, -40)})
        points.append({"at": at, "zones": zones})
    return points
