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
