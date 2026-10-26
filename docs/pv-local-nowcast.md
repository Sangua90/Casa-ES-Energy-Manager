# Local PV nowcast

Current PV availability is estimated from the personal weather station's
irradiance and aligned recorder five-minute statistics. A seven-day rolling
window trains on five completed days and validates on two separate days. It is
accepted only with at least 90 training samples, 30 validation samples, and
10% lower mean absolute error than the contemporary weather forecast.

Hours require 12 samples across at least three training days. Near-full
batteries (SOC >=90%) and output >=90% of nominal panel power are excluded as
potentially curtailed. These are filters, not proof that all remaining samples
are unclipped. Hourly ratios are smoothed between bin centers. Extrapolation
beyond the observed irradiance range and models older than 14 days are rejected.

Learning repeats daily through the integration, with persistence in HA Store.
Changes to panel power invalidate the former model. The physical panel power
comes from the existing Open-Meteo Solar Forecast configuration, in W.

Two persistent dashboard helpers control a positive bias: default +3%, capped
at 200 W. The hypothetical estimate is capped at installed nominal DC power;
the final PV PRO value remains max(actual inverter power, hypothetical power).
Measured production is never reduced by the estimate. Electricity guards,
SOC allocation and compressor protections remain authoritative.

If live irradiance is unavailable, has invalid units, or is older than five
minutes, or the model is unvalidated/outside its learned hours, use the existing
Open-Meteo current-power forecast. Future hourly/daily energy curves remain
Open-Meteo forecasts; this does not replace tomorrow's weather forecast.

The dashboard input_boolean energy_manager_stima_fv_locale selects local learning.
It remains off until the current panel layout is confirmed to cover the training
window; turning it off returns to weather forecasts and preserves actual power.


## Current panel layout
The 16 × 545 W layout (8,720 W) entered service on 9 October 2026 around 16:00 Europe/Rome. The date-only helper `input_datetime.energy_manager_pannelli_attuali_dal` is set to 2026-10-09. Training excludes the whole installation day, starting on 10 October; this avoids mixing both layouts. Changing this date invalidates the old model. Missing or invalid dates disable local learning. The model remains on weather fallback until the independent training and validation criteria pass. The persistent local-learning selector can then use daily automatic retraining.


## Additional inverter telemetry
Verified sources: `sensor.inverter_solarman_pv_power_1` and `_2` in W (median sum error 0 W over 302 significant five-minute samples on the new layout). Available string voltage/current provide diagnosis, not extra additive power. Training checks string sum against total within max(100 W, 5%). Optional signed battery power (positive charging) and grid power reject periods at >=3,300 W charging with abs(grid)<=100 W: clipping cannot be excluded near the configured 3.5 kW battery acceptance limit. This is conservative filtering, not proof of clipping. SOC>=90% remains excluded. Weather-station outdoor temperature and wind are available, but no unvalidated temperature/wind multiplier is applied without panel characteristics and independent evidence. The inverter cloud-cover sensor is a meteorological estimate, not an independent local measurement. Tomorrow's forecast continues to use Open-Meteo; current irradiance cannot predict tomorrow's clouds.
