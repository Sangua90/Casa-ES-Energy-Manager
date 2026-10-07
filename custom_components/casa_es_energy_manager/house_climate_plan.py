"""Room routines and marginal heat costs, independent of Home Assistant I/O."""
from datetime import datetime, timedelta
from math import isfinite

HOUSE_TYPE = "house_climate"
ROOM_TYPE = "climate_room"


def finite(value, default=None):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def celsius(value, unit):
    value = finite(value)
    if value is None or unit not in ("°C", "C", "°F", "F"):
        return None
    value = (value - 32) * 5 / 9 if unit in ("°F", "F") else value
    return value if -10 <= value <= 50 else None


def auxiliary_active(state, attributes):
    if state in ("unknown", "unavailable"):
        return True
    value = finite(state)
    unit = attributes.get("unit_of_measurement")
    if value is not None and unit in ("W", "kW"):
        return value * (1000 if unit == "kW" else 1) > 100
    return state not in ("off", "idle", "standby", "ready", "0")



def occupancy(room, now):
    """A cross-midnight period belongs to the day on which it STARTED."""
    if room.get("manual_only", False):
        return False, False
    preheat = timedelta(minutes=finite(room.get("preheat_minutes"), 90))
    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        prefix = "weekend" if day.weekday() >= 5 else "weekday"
        for suffix in ("", "_second"):
            if not room.get(prefix + suffix + "_enabled", False):
                continue
            start_time = room.get(prefix + suffix + "_start", "00:00:00")
            end_time = room.get(prefix + suffix + "_end", "00:00:00")
            start = datetime.combine(day, datetime.fromisoformat("2000-01-01T" + start_time).time(), now.tzinfo)
            end = datetime.combine(day, datetime.fromisoformat("2000-01-01T" + end_time).time(), now.tzinfo)
            if end <= start:
                end += timedelta(days=1)
            if start.timestamp() <= now.timestamp() < end.timestamp():
                return True, False
            # Use epoch seconds so advance notice is correct across DST.
            if start.timestamp() - preheat.total_seconds() <= now.timestamp() < start.timestamp():
                return True, True
    return False, False


def situation(house, now):
    """Expired overrides return to routines; never change learned habits."""
    exception = house.get("exception", {})
    try:
        expiry = datetime.fromisoformat(exception.get("expires_at", ""))
        if expiry.tzinfo and expiry.timestamp() > now.timestamp():
            return exception.get("mode", "normal")
    except (ValueError, TypeError):
        pass
    return "normal"


def next_deadline(room, now):
    if room.get("manual_only"):
        return None
    starts = []
    for offset in (0, 1, 2):
        day = now.date() + timedelta(days=offset)
        prefix = "weekend" if day.weekday() >= 5 else "weekday"
        for suffix in ("", "_second"):
            if room.get(prefix + suffix + "_enabled"):
                start = datetime.combine(day, datetime.fromisoformat("2000-01-01T" + room[prefix + suffix + "_start"]).time(), now.tzinfo)
                if start.timestamp() > now.timestamp():
                    starts.append(start)
    return min(starts, key=lambda d: d.timestamp()) if starts else None


def target_level(house, room, now):
    occupied, preparing = occupancy(room, now)
    mode = situation(house, now)
    maintenance = finite(room.get("maintenance_temperature"), finite(room.get("base_temperature"), 17))
    base = finite(room.get("day_base_temperature"), 19)
    if mode in ("away", "weekend_away", "holiday"):
        return maintenance, "maintenance", False, False
    if mode == "home" and not room.get("manual_only"):
        occupied = True
    if occupied:
        return min(finite(room.get("comfort_temperature"), 21), 22), "comfort", occupied, preparing
    if room.get("base_enabled") and not room.get("manual_only"):
        return min(base, 22), "base", False, False
    return maintenance, "maintenance", False, False


def heat_costs(house, room):
    electricity = finite(house.get("electricity_price"), 0.30)
    gas_price = finite(house.get("gas_price"), 1.0)
    gas_energy = finite(house.get("gas_energy_kwh_sm3"), 10.7)
    efficiency = finite(house.get("gas_efficiency"), 0.9)
    cop = finite(room.get("heat_pump_cop"))
    gas_cost = gas_price / (gas_energy * efficiency) if gas_energy > 0 and 0 < efficiency <= 1 else None
    hp_cost = electricity / cop if cop is not None and cop > 0 else None
    return {"gas_eur_kwh_heat": gas_cost, "heat_pump_eur_kwh_heat": hp_cost,
            "break_even_cop": electricity / gas_cost if gas_cost else None,
            "economics_verified": bool(house.get("economics_confirmed") and room.get("cop_confirmed")
                                       and gas_cost is not None and hp_cost is not None)}


def room_plan(house, room, now, current, solar_available, electrical_ok):
    target, level, occupied, preparing = target_level(house, room, now)
    base = finite(room.get("base_temperature"), 17)
    comfort = target
    cooling = finite(room.get("cooling_temperature"), 26)
    cost = heat_costs(house, room)
    season = house.get("season", "shoulder")
    result = {"room": room.get("name", "Stanza"), "occupied": occupied,
              "preparing": preparing, "current_temperature": current,
              "radiator_target": base if season == "winter" else None, "heat_pump_mode": "off", "heat_pump_target": None,
              "source": "base_gas" if season == "winter" else "none", "target_level": level,
              "room_target": target, **cost}
    if current is None:
        result.update(source="temperature_unavailable", radiator_target=None)
        return result
    if not room.get("reviewed", False):
        result["source"] = "profile_to_confirm"
        return result
    if not occupied and level != "base":
        return result
    has_hp = bool(room.get("heat_pump_entity"))
    affordable = (cost["economics_verified"] and cost["heat_pump_eur_kwh_heat"] < cost["gas_eur_kwh_heat"] * 0.95)
    if season == "summer":
        result["radiator_target"] = None
        if has_hp and electrical_ok and solar_available and current > cooling + 0.3:
            result.update(source="solar_cooling", heat_pump_mode="cool", heat_pump_target=cooling)
    elif season == "shoulder":
        result["radiator_target"] = None
        if has_hp and electrical_ok and solar_available and current < comfort - 0.3:
            result.update(source="solar_heating", heat_pump_mode="heat", heat_pump_target=comfort)
    elif season == "winter":
        if has_hp and electrical_ok and (solar_available or (house.get("allow_economic_grid", False) and affordable)):
            if current < comfort - 0.3:
                result.update(source="solar_heating" if solar_available else "economic_heat_pump",
                              heat_pump_mode="heat", heat_pump_target=comfort)
        elif room.get("radiator_entities"):
            result.update(source="gas_comfort", radiator_target=comfort)
    return result
