"""Adaptive PV-potential estimator for Casa ES Energy Manager v1.4.1.

The zero-export inverter can hide available PV production by curtailing the
array when house load and battery charge cannot absorb it.  This estimator
combines two optional instantaneous forecast sources and learns small bias
corrections whenever measured PV is likely to be unconstrained.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class _SourceState:
    factor: float = 1.0
    relative_error: float = 0.20
    samples: int = 0


class AdaptivePVPotentialEstimator:
    """Fuse custom and provider PV estimates conservatively.

    Source 1 is the user supplied ``pv_potential_power_sensor``.  In the Casa ES
    installation this is intended for the custom virtual forecast that already
    follows the array well.

    Source 2 is ``pv_forecast_current_hour_sensor`` when that sensor exposes
    power (W/kW/MW).  This remains a secondary independent reference.

    The estimator never reports less than measured PV and only learns forecast
    bias on samples where curtailment is unlikely.  A small confidence-dependent
    haircut is applied only to the *hidden* part above measured PV so uncertain
    forecasts cannot aggressively create grid import.
    """

    def __init__(self) -> None:
        # The custom estimate starts with a better prior because it is explicitly
        # the site-calibrated virtual sensor.  Observed error can still move the
        # weighting toward the provider later.
        self.custom = _SourceState(relative_error=0.12)
        self.provider = _SourceState(relative_error=0.28)

    @staticmethod
    def _is_observable(
        *,
        measured_w: float,
        grid_import_w: float,
        custom_w: float | None,
        provider_w: float | None,
    ) -> bool:
        """Return True only when measured PV is reasonably usable for learning."""
        # Clear grid import is the strongest indication that PV is not being
        # curtailed: demand is greater than solar supply.
        if grid_import_w >= 250.0:
            return True

        candidates = [
            value for value in (custom_w, provider_w) if value is not None and value > 100.0
        ]
        if not candidates:
            return False

        # Also accept near-forecast operation.  This captures useful sunny
        # samples without learning from obvious zero-export clipping.
        return measured_w >= 0.95 * max(candidates)

    @staticmethod
    def _learn_source(state: _SourceState, forecast_w: float | None, measured_w: float) -> None:
        if forecast_w is None or forecast_w < 100.0 or measured_w < 50.0:
            return

        ratio = _clamp(measured_w / forecast_w, 0.55, 1.45)
        corrected_before = forecast_w * state.factor
        rel_error = abs(corrected_before - measured_w) / max(measured_w, 250.0)
        rel_error = _clamp(rel_error, 0.0, 2.0)

        alpha = 0.06
        state.factor = _clamp((1.0 - alpha) * state.factor + alpha * ratio, 0.65, 1.35)
        state.relative_error = _clamp(
            (1.0 - alpha) * state.relative_error + alpha * rel_error,
            0.03,
            1.50,
        )
        state.samples += 1

    @staticmethod
    def _weight(state: _SourceState) -> float:
        # Inverse error gives a stable, interpretable preference while the floor
        # avoids one source becoming permanently dominant after a lucky sample.
        return 1.0 / max(state.relative_error, 0.05)

    def update(
        self,
        *,
        measured_pv_w: float,
        load_w: float,
        grid_power_w: float,
        battery_power_w: float,
        custom_forecast_w: float | None,
        provider_forecast_w: float | None,
        inverter_limit_w: float | None = None,
    ) -> dict[str, Any]:
        """Return the fused potential estimate plus diagnostic fields."""
        measured = max(float(measured_pv_w), 0.0)
        custom = None if custom_forecast_w is None else max(float(custom_forecast_w), 0.0)
        provider = None if provider_forecast_w is None else max(float(provider_forecast_w), 0.0)
        grid_import = max(float(grid_power_w), 0.0)

        observable = self._is_observable(
            measured_w=measured,
            grid_import_w=grid_import,
            custom_w=custom,
            provider_w=provider,
        )
        if observable:
            self._learn_source(self.custom, custom, measured)
            self._learn_source(self.provider, provider, measured)

        corrected_custom = custom * self.custom.factor if custom is not None else None
        corrected_provider = provider * self.provider.factor if provider is not None else None

        available: list[tuple[str, float, float]] = []
        if corrected_custom is not None:
            available.append(("custom", corrected_custom, self._weight(self.custom)))
        if corrected_provider is not None:
            available.append(("provider", corrected_provider, self._weight(self.provider)))

        if not available:
            raw_fused = measured
            source = "measured_only"
            agreement = 1.0
        elif len(available) == 1:
            source, raw_fused, _ = available[0]
            source = f"{source}_only"
            agreement = 0.72
        else:
            total_weight = sum(item[2] for item in available)
            raw_fused = sum(item[1] * item[2] for item in available) / total_weight
            source = "blended"
            spread = abs(available[0][1] - available[1][1])
            agreement = 1.0 - _clamp(spread / max(raw_fused, 500.0), 0.0, 1.0)

        sample_strength = _clamp((self.custom.samples + self.provider.samples) / 80.0, 0.0, 1.0)
        history_quality = 1.0 - _clamp(
            min(self.custom.relative_error, self.provider.relative_error), 0.0, 0.8
        )
        confidence = _clamp(
            0.38 * agreement + 0.32 * history_quality + 0.30 * sample_strength,
            0.20,
            0.98,
        )
        if source == "measured_only":
            confidence = 1.0

        raw_fused = max(raw_fused, measured)
        if inverter_limit_w is not None and inverter_limit_w > 0:
            raw_fused = min(raw_fused, float(inverter_limit_w))

        hidden_raw = max(raw_fused - measured, 0.0)
        hidden_trust = 0.70 + 0.30 * confidence
        estimate = measured + hidden_raw * hidden_trust

        return {
            "pv_potential_w": round(estimate, 1),
            "pv_potential_raw_fused_w": round(raw_fused, 1),
            "pv_potential_gap_w": round(max(estimate - measured, 0.0), 1),
            "pv_potential_after_house_w": round(max(estimate - max(load_w, 0.0), 0.0), 1),
            "pv_estimator_confidence": round(confidence * 100.0, 1),
            "pv_estimator_source": source,
            "pv_estimator_learning_sample": observable,
            "pv_estimator_custom_input_w": custom,
            "pv_estimator_provider_input_w": provider,
            "pv_estimator_custom_corrected_w": (
                round(corrected_custom, 1) if corrected_custom is not None else None
            ),
            "pv_estimator_provider_corrected_w": (
                round(corrected_provider, 1) if corrected_provider is not None else None
            ),
            "pv_estimator_custom_factor": round(self.custom.factor, 4),
            "pv_estimator_provider_factor": round(self.provider.factor, 4),
            "pv_estimator_custom_error_pct": round(self.custom.relative_error * 100.0, 1),
            "pv_estimator_provider_error_pct": round(self.provider.relative_error * 100.0, 1),
            "pv_estimator_custom_samples": self.custom.samples,
            "pv_estimator_provider_samples": self.provider.samples,
            "pv_estimator_grid_import_w": round(grid_import, 1),
            "pv_estimator_battery_power_w": round(float(battery_power_w), 1),
        }
