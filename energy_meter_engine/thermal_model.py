"""Robust room estimator and contextual rates. No hardware limits are learned."""
from statistics import median
from house_climate_plan import finite


def estimate(zone, model):
    sensors = zone.get("sensors", {})
    independent = finite(zone.get("independent_temperature"))
    context = zone.get("thermal_source", "unknown")
    bias = model.setdefault("sensor_bias", {})
    if independent is not None:
        for entity, value in sensors.items():
            value = finite(value)
            if value is None or abs(value - independent) > 5:
                continue
            learned = bias.setdefault(entity + ":" + context, {"values": []})
            stamp = finite(zone.get("timestamp"), 0)
            if stamp and stamp - learned.get("updated_at", 0) < 900:
                continue
            learned["updated_at"] = stamp
            learned["values"] = (learned["values"] + [value - independent])[-96:]
            learned.update(offset_c=median(learned["values"]), samples=len(learned["values"]),
                           confidence=min(len(learned["values"]) / 40, .9))
        return independent, "independent"
    values = []
    for entity, value in sensors.items():
        learned = bias.get(entity + ":" + context, {})
        if finite(value) is not None and learned.get("samples", 0) >= 12:
            values.append(value - learned["offset_c"])
    return (median(values), "calibrated_fusion") if values else (finite(zone.get("temperature")), "uncalibrated_fallback")


def fit_context(model, observation):
    samples = model.setdefault("observations", [])
    samples.append(observation)
    del samples[:-192]
    # A constrained RC loss coefficient is identifiable only with varied
    # indoor/outdoor gradients and off samples. Never infer COP from rates.
    off = [s for s in samples if s["source"] == "off" and s.get("outdoor") is not None
           and s["temperature"] - s["outdoor"] >= 3 and s["rate"] < 0 and finite(s.get("solar_w"), 0) < 100]
    if len(off) >= 12:
        coefficients = [-s["rate"] / (s["temperature"] - s["outdoor"]) for s in off]
        loss = max(.005, min(median(coefficients), .3))
        model["envelope"] = {"loss_per_hour": loss, "samples": len(off),
                             "confidence": min(len(off) / 60, .85), "updated_at": observation["at"]}
    loss = model.get("envelope", {}).get("loss_per_hour")
    solar_samples = [s for s in samples if s["source"] == "off" and finite(s.get("solar_w"), 0) > 500 and s.get("outdoor") is not None]
    if loss is not None and len(solar_samples) >= 20:
        gains = [(s["rate"] + loss * (s["temperature"] - s["outdoor"])) / (s["solar_w"] / 1000) for s in solar_samples]
        model["solar_gain"] = {"c_h_per_pv_kw": max(0, min(median(gains), .2)), "samples": len(gains),
                               "confidence": min(len(gains) / 120, .5), "proxy": "measured_pv_not_irradiance", "updated_at": observation["at"]}
    for source in ("heat_pump", "gas", "combined", "cooling"):
        selected = [s for s in samples if s["source"] == source]
        if len(selected) >= 12:
            loss = model.get("envelope", {}).get("loss_per_hour", 0)
            gains = [s["rate"] + loss * (s["temperature"] - s["outdoor"]) if s.get("outdoor") is not None else s["rate"] for s in selected]
            model.setdefault("gains", {})[source] = {"rate_c_h": max(-5, min(median(gains), 5)),
                "samples": len(selected), "confidence": min(len(selected) / 60, .85), "updated_at": observation["at"]}
    # Cross-zone warming is reported only with an off receiving room, a
    # configured adjacent zone and repeated positive residual observations.
    links = model.setdefault("cross_zone", {})
    if observation["source"] == "off":
        for entity, gradient in observation.get("neighbor_gradients", {}).items():
            if gradient > 1 and observation["rate"] > .05:
                link = links.setdefault(entity, {"values": []})
                link["values"] = (link["values"] + [observation["rate"] / gradient])[-96:]
                link.update(coefficient=min(median(link["values"]), .15), samples=len(link["values"]),
                            confidence=min(len(link["values"]) / 80, .7))


def predicted_rate(model, source, temperature, outdoor, neighbors=None, solar_w=0):
    loss = model.get("envelope", {}).get("loss_per_hour")
    gain = model.get("gains", {}).get(source, {}).get("rate_c_h")
    legacy = model.get(source, {}).get("rate_c_h")
    if loss is not None and outdoor is not None:
        rate = -loss * (temperature - outdoor) + (gain or 0)
    else:
        rate = finite(legacy, -.15 if source == "off" else -.7 if source == "cooling" else .7)
    rate += model.get("solar_gain", {}).get("c_h_per_pv_kw", 0) * max(finite(solar_w, 0), 0) / 1000
    for key, gradient in (neighbors or {}).items():
        link = model.get("cross_zone", {}).get(key, {})
        if link.get("samples", 0) >= 20:
            rate += link["coefficient"] * max(gradient, 0)
    return max(-5, min(rate, 5))
