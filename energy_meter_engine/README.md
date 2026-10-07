# Energy Meter Engine

Authenticated internal numerical planner. The add-on has no Home Assistant API,
device-service permissions, host access or exposed host ports. Integration 1.5.22
provides telemetry and retains the final electrical, hydraulic and manual-control guards.

Configure a random `api_token` of at least 32 characters before starting. Set the
house profile `engine_url` to `http://<addon-container-host>:8099` and use the same
token in `engine_token`. Do not use a Home Assistant access token.

Version 0.1.0 provides versioned planning, aggregated thermal-rate learning,
sample counts/confidence, contamination filtering, bounded model persistence,
energy-budget diagnostics and significant-decision history. New profiles default
to observation. COP remains a configured estimate until independently verified;
thermal warm-up speed alone cannot measure COP.

The first release is an observation/calibration foundation, not implementation of
every feature in the full specification. Independent sensor bias estimation,
cross-zone models, gas-reading campaigns, house exceptions and full joint horizon
optimization still require further implementation and validation.

