# Energy Meter Engine 0.2.2

Authenticated numerical thermal planner on the Home Assistant internal network.
The add-on has no HA access token or device-service permissions. Integration
1.5.23 retains all electrical, hydraulic, compressor and manual-ownership guards.

- Recorder bootstrap: ten days in daily executor queries, full attributes,
  aware timestamps, absolute 15-minute replay, checkpointed authenticated uploads.
- Independent room reference and contextual, robust sensor bias; calibrated
  fusion only after sufficient reference samples. No universal fixed offset.
- Robust off/gas/heat-pump/combined/cooling rates, outdoor loss coefficient,
  configured adjacent-room effects, stop-cycle inertia and overshoot.
- Adaptive conservative preparation time, three target levels, overnight
  routines, explicit expiring house situations that never teach normal habits.
- Shared 24-hour thermal forecast with 15-minute steps, priorities, weather,
  battery input need, base load and certified thermal overflow. Multisplit
  group power is reserved once. Real starts retain conservative per-head guards.
- Separate group consumption samples by active head count; no COP inferred
  from temperature rise. Marginal-price/COP confirmation gates grid operation.
- Significant decision journal, confidence, model timestamps and diagnostics.
- Gas reading deltas refer to the whole house including ACS, never one room.

The optimizer is a bounded priority dispatch, not a claim of globally optimal
MPC. Unknown future solar is not spent. Weather absence and uncalibrated thermal
rates are explicit forecast assumptions. Calibration continues during operation.
Automatic gas and seasonal Ariston changes require confirmed hardware facts.
Existing routines remain authoritative; phone/motion alone cannot cancel them.

Configure a random API token of at least 32 characters, the internal engine URL
and the same token in the house profile. Never use an HA access token. No host
ports or device privileges are required. /health checks startup; /v1/plan and
/v1/history require authentication. Models persist atomically under /data.
