# House climate 1.5.23 / Engine 0.2.0

The Engine imports attribute-preserving Recorder history through the integration,
learns contextual temperature bias and thermal dynamics, and forecasts 24 hours.
The integration retains final electrical/hydraulic guards and controls devices.

Profiles can activate independently: one room awaiting verification does not
block verified rooms. Hydraulic confirmation gates *both* valves and gas calls.
Gas remains separate from domestic hot water and the Ariston main power.

House situations expire explicitly: home 12 h, short absence 4 h, weekend away
48 h, holiday 7 days; routines resume at expiry. Exceptions never train normal
habits. Window/auxiliary-source/unknown-state intervals are excluded. Numeric
auxiliary power uses W/kW thresholds, so oven standby does not contaminate all
kitchen history. Movement and phone data remain secondary evidence.

Compressor dwell is based on the last mode transition, independently of setpoint
changes. User mode, temperature and fan changes release automatic ownership.
Grid overload persisting 15 s or inverter/phase critical loads trigger priority
shedding, including manual HVAC, with a 20-minute restart hold. Ariston main power
and native anti-legionella are never part of this guard.

The isolated Engine plans within verified energy budgets. Shared outdoor-unit
power is not summed from duplicate indoor telemetry. Live starts still reserve
conservative full headroom until group consumption is physically verified.

Room/model diagnostics expose reference quality, sample counts, confidence,
preheat time, inertia, a shared horizon, group measurements and whole-house gas
reading deltas. Unknown COP, hydraulic flow, physical phases and boiler facts
remain confirmation gates; they cannot be learned by relaxing hardware limits.

The profile reconfigure flow preserves the reconfigure step on submission;
returning the create step would reject an existing house as a duplicate. Real
Home Assistant smoke coverage checks this behavior and field serialization.


## Live PV thermal storage settings

Solar heat-pump dispatch is independent of the gas/TRV occupancy timetable.
Ordinary surplus can prepare rooms up to their configured comfort temperature;
large measured or guarded curtailment surplus can store additional heat/coolth.
Existing descending room priorities, electrical budgets, manual/window guards,
and compressor minimum on/off times remain authoritative.

The Clima dashboard exposes persistent input-number helpers:
- energy_manager_margine_accumulo_termico: 0–3 C (default 2); zero disables the additional storage band.
- energy_manager_limite_caldo_accumulo: 22–25 C (default 24).
- energy_manager_limite_freddo_accumulo: 22–26 C (default 24).
- energy_manager_soglia_surplus_accumulo: 1–2 times nominal unit power (default 1.25).

The integration reads these each cycle and forwards settings to Engine 0.2.7.
Missing/invalid helpers use bounded defaults. Helpers have no forced initial
value after creation, so user edits are restored after Home Assistant restarts.
The actual heat-pump target, rather than the gas maintenance target, determines
coasting; calibrated device setpoints respect the selected storage limits.


### PV storage fan
The persistent dashboard selector `input_boolean.energy_manager_ventola_alta_surplus` enables High fan only for automatically owned heat pumps during solar thermal storage. Supported fan modes are checked. The prior fan setting is stored in the existing persisted ownership record and restored when storage ends or the selector is disabled. Manual fan changes and manually running units are not overwritten. Fan changes preserve the compressor mode timestamp; a telemetry grace period avoids mistaking our pending fan command for a manual override. Heating storage is configured at +5 C, ceiling 27 C; cooling margin is separate at 2 C, floor 24 C.


## Shared external-unit power, 13 October correction
P1 power is the single total of the outdoor unit and all three indoor fans. Live dispatch now reserves only the incremental group total, never duplicated nominal power per head. At least12 recent valid samples (<=7days, excluding standby) provide an arithmetic mean; phase and inverter checks use35%+150W startup margin capped by group nominal. A larger observed head-count bucket can bound a smaller request. Missing or stale samples fall back to conservative group nominal. Engine tracks heat/cool mode buckets separately and its horizon reserves group maximum once. Starts remain staggered120s and native operating limits remain in force.
Optional small grid support is prepared but disabled when its helper is absent. Automatic approval review blocked creating/setting the helper and dashboard; no new grid support is activated by this update. The temperature discrepancy between TRVs and Midea sensors still requires clarification; a heating command does not guarantee compressor activity.


## Confirmed TRV reference
Per-room persistent selectors `input_boolean.energy_manager_riferimento_testine_camera_es` and `_palestra` are enabled following user confirmation on14October. The radiator sensors are more representative than head-mounted split sensors, but may overread when radiators heat. A bounded native-minus-TRV offset (<=10C) is acquired only from available TRVs reported within6hours, while neither radiators nor the heat pump are heating. Cold offsets persist through restarts; while radiators heat or for1hour after gas stop they are frozen, not recalibrated. No valid cold offset means no invented compensation. A seven-day expiry applies to stored offsets. Hardware min/max temperature clamps compensated device setpoints. The room goal remains unchanged. Ordinary room ceiling/floor, window, surplus and admission decisions respect the persisted 20-minute ON and 5-minute OFF dwell. Only the verified electrical emergency guard may bypass the ON dwell; native device protections remain autonomous. No independent room thermometer was found for these two rooms; warm TRVs remain a conservative upper bound, not an exact estimate of true room temperature.
