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
