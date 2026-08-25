# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Version 1.1.0 - simplified field-validation release**  
> Electrical decisions are deterministic and local. AI remains advisory. Automatic real appliance switching is intentionally still disabled while admission logic is validated with real Casa ES data. Manual emergency grid charging is available only through explicit Home Assistant scripts configured by the user.

## Guided configuration

Initial setup and later Options are split into readable sections:

1. electrical sensors
2. electrical limits and protection
3. solar forecast
4. battery and energy strategy
5. advisory AI planner
6. summary

Fields marked with `*` are optional. Required sensors are validated for compatible units.

## Global energy preference

Choose one global preference without changing appliance priorities:

- **Batteria prioritaria**: keeps a larger energy reserve before admitting flexible loads.
- **Bilanciata**: default compromise.
- **Carichi prioritari**: uses more available PV on flexible loads while still respecting hard electrical limits and definite battery-target shortfalls.

Measured grid, phase and inverter limits always override the selected preference.

v1.1 also exposes **Strategia energetica** as a Home Assistant select entity so the preference can be changed directly from a compact dashboard view without reopening integration Options.

## Managed devices

Managed devices are configured separately from the initial wizard. v1.1 deliberately simplifies the flow to two sections: **Base** and **Vincoli e tempi**.

Important fields include:

- Home Assistant entity to manage
- optional real power sensor
- initial conservative power estimate
- numeric priority `1..100` (`1` = highest, `100` = lowest)
- electrical phase L1/L2/L3/three-phase
- optional expected cycle/runtime
- optional minimum ON time
- optional minimum OFF time
- minimum battery SOC
- grid integration permission and per-device grid limit
- minimum daily runtime, maximum daily runtime and activations
- allowed time window and deadline
- on-only / non-interruptible-cycle behavior
- preemption protection
- large-consumer settings

Wallbox/EV dynamic-current controls and device dependencies are intentionally removed from the active v1.1 model because they are not part of the Casa ES load set. Older stored subentries remain readable; obsolete keys are ignored and removed when the device is reconfigured.

### Automatico / Manuale / Spento

Every managed device exposes its own Home Assistant select entity:

- **Automatico**: Casa ES evaluates the device normally.
- **Manuale**: Casa ES observes its real consumption but excludes it from automatic decisions; the user remains in control.
- **Spento**: Casa ES excludes the device from automatic candidates. This is useful for seasonal loads such as pool equipment.

The mode is restored after Home Assistant restarts. Even when a device is Manuale or Spento, measured grid/inverter/phase totals remain authoritative for electrical safety.

> In the current field-validation release these modes affect deterministic decisions/dry-run only. They do not yet physically switch normal managed appliances.

### Optional cycle and anti-cycling values

Expected cycle duration, minimum ON and minimum OFF are optional in v1.1.

- If expected runtime is omitted, Casa ES does not invent a synthetic one-hour cycle or reserve fictional cycle energy.
- If minimum ON is omitted, no minimum-ON constraint is applied.
- If minimum OFF is omitted, no minimum-OFF constraint is applied.

This keeps simple loads simple while still allowing explicit anti-cycling protection for heat pumps and inverter climates.

## Adaptive variable-load learning

Adaptive learning now needs only the **real power sensor**. A separate climate-specific learning configuration is not required.

For any managed device with adaptive learning enabled and a real power sensor, Casa ES records persistent statistics such as active samples, mean, variation, minimum and maximum observed power and derives a conservative admission estimate.

When the managed entity itself is `climate.*`, Casa ES also uses its HVAC mode/action to keep more precise mode-specific profiles. For non-climate loads, a general profile is learned directly from the watt variation.

The learned estimate is only used to decide whether a future start looks reasonable. Actual electrical safety always comes from measured grid, inverter and L1/L2/L3 sensors.

## Daily runtime fields

Minimum/maximum daily runtime and daily activation count remain available, useful for loads such as the pool filter. These fields are retained in the configuration and diagnostics while real scheduler/runtime enforcement remains part of the later control phase.

## Read-only monitored loads

Appliances that Casa ES must never control can be added as **Monitored loads** with:

- name
- real power sensor
- phase
- enabled state

These loads explain the measured phase consumption without double counting it. Example: if L1 measures 2600 W and known appliances account for 2200 W, Casa ES reports 2200 W recognized and 400 W other load. The safety calculation still uses the real 2600 W phase measurement.

## Solar and battery planning

Casa ES separates measured PV power, potential/unconstrained PV forecast and future forecast energy to the battery target.

Potential PV and forecast data improve planning but never replace measured electrical safety values. The selected Battery/Balanced/Loads preference adjusts only the conservative flexible-energy reservation and cannot weaken electrical protection or override a definite forecast shortfall.

## Emergency battery charge from grid

Casa ES exposes:

- **Avvia ricarica di emergenza batteria**
- **Interrompi ricarica di emergenza batteria**

Because inverter controls differ between installations, Casa ES does not guess inverter services. In Options, configure two explicit Home Assistant `script.*` entities: one to start grid charging and one to stop it.

The start script receives:

- `power_w`
- `target_soc`
- `max_minutes`

Casa ES automatically requests the stop script when target SOC is reached, the timeout expires, or an electrical protection condition requires it. The buttons remain unavailable until both scripts are configured.

## Compact Casa ES Manager view

v1.1 is designed to support a small day-to-day Home Assistant view containing only:

- **Strategia energetica**
- emergency grid-charge start/stop
- one **Automatico / Manuale / Spento** selector for every managed device

It intentionally does not duplicate PV/house/grid charts that already exist elsewhere in the user's dashboard. An example Lovelace view is provided under `examples/` and can be adapted to the entity IDs created by Home Assistant.

## Advisory AI

Gemini / AI Task remains advisory. AI recommendations cannot override local deterministic guardrails.

## Diagnostics and field validation

Download diagnostics from:

`Settings -> Devices & services -> Casa ES Energy Manager -> Download diagnostics`

Useful diagnostics include source sensors, forecast inputs, planner policy, energy preference, phase headroom/attribution, managed-device configuration, runtime mode, real device power, adaptive learned profiles and dry-run decisions/reasons.

Automatic appliance switching should remain disabled until dry-run decisions have been reviewed against real behavior.

## Installation / update

Add this repository to HACS as a custom Integration repository:

`https://github.com/Sangua90/Casa-ES-Energy-Manager`

Install/update **Casa ES Energy Manager**, restart Home Assistant, then open:

`Settings -> Devices & services -> Casa ES Energy Manager`

Existing v1.0 configurations remain readable. Reconfiguring an old managed device automatically removes obsolete wallbox/EV/dependency fields.

## Safety architecture

Safety priority is always:

1. measured grid / phase / inverter limits
2. deterministic local protection
3. battery target and user-selected energy preference
4. managed-device constraints and anti-cycling
5. forecast optimization
6. AI advice

Forecast and AI never bypass electrical protection.

## Origin and license

Casa ES Energy Manager is a modified/derived project based on `InventoCasa/PV-Excess-Control` and retains GNU Affero General Public License v3 requirements and notices. See `LICENSE` and `NOTICE.md`.
