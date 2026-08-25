# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Version 1.0.0 - field-validation release**  
> Electrical decisions are deterministic and local. AI remains advisory. Automatic real appliance switching is intentionally still disabled while the v1 admission logic is validated with real Casa ES data. Manual emergency grid charging is available only through explicit Home Assistant scripts configured by the user.

## v1.0 highlights

### Guided configuration

Initial setup and later Options are split into readable sections:

1. electrical sensors
2. electrical limits and protection
3. solar forecast
4. battery and energy strategy
5. advisory AI planner
6. summary

Fields marked with `*` are optional. Required sensors are validated for compatible units.

### Global energy preference

Choose one global preference without changing appliance priorities:

- **Battery first**: keeps a larger energy reserve before admitting flexible loads.
- **Balanced**: default compromise.
- **Loads first**: uses more available PV on flexible loads while still respecting hard electrical limits and definite battery-target shortfalls.

Measured grid, phase and inverter limits always override the selected preference.

## Managed devices

Managed devices are configured separately from the initial wizard. Their configuration is split into Base, Constraints/Times, and Advanced/EV sections.

Important fields include:

- Home Assistant entity to manage
- optional real power sensor
- initial conservative power estimate
- numeric priority `1..10` (`1` = highest)
- electrical phase L1/L2/L3/three-phase
- expected runtime
- minimum battery SOC
- grid integration permission and per-device grid limit
- minimum daily runtime, maximum daily runtime and activations
- allowed time window and deadline
- dependencies
- on-only / non-interruptible-cycle behavior
- preemption protection
- large-consumer settings
- dynamic-current / EV fields retained from the original PV Excess model

### AUTO / OVERRIDE / OFF

Every managed device exposes its own Home Assistant select entity:

- **AUTO**: Casa ES evaluates the device normally.
- **OVERRIDE**: Casa ES observes its power but excludes it from automatic decisions; the user remains in manual control.
- **OFF**: Casa ES excludes the device and marks it as a load that must remain off when real control is enabled in a later validated release.

The mode is restored after Home Assistant restarts.

### Minimum ON / minimum OFF protection

Each managed device has two independent anti-cycling values:

- minimum time ON
- minimum time OFF

Both default to **20 minutes**, suitable as a conservative starting point for heat pumps and inverter climates. They can be reduced to zero for loads that do not need anti-cycling.

For a climate entity, compressor modulation is not interpreted as Casa ES switching the appliance off. A climate may remain in `cool` or `heat` while its real power falls close to zero and later rises again.

## Adaptive climate / heat-pump learning

When **adaptive power profile** is enabled on a `climate.*` managed device and a real power sensor is configured, Casa ES learns the electrical behavior automatically.

It records persistent statistics separately for HVAC modes such as:

- cooling
- heating
- dry/dehumidification
- fan-only

The learner uses the real power sensor and also records `hvac_action`. During the initial learning period Casa ES uses the configured conservative power estimate. After enough active samples, it derives a high-side admission estimate from observed mean, variation and maximum draw.

The profile survives Home Assistant restarts.

The learned estimate is only used to decide whether a future start looks reasonable. Actual electrical safety always comes from measured grid, inverter and L1/L2/L3 sensors.

## Read-only monitored loads

Appliances that Casa ES must never control can be added as **Monitored loads** with:

- name
- real power sensor
- phase
- enabled state

These loads explain the measured phase consumption without double counting it. Example: if L1 measures 2600 W and known appliances account for 2200 W, Casa ES reports 2200 W recognized and 400 W other load. The safety calculation still uses the real 2600 W phase measurement.

## Solar and battery planning

Casa ES separates:

- measured PV power from the inverter
- potential/unconstrained PV forecast
- future forecast energy to the battery target

Potential PV and forecast data improve planning but never replace measured electrical safety values.

The local policy calculates battery energy still required, charging efficiency, expected base-house load, forecast energy to target, flexible-load energy budget, grid/inverter/phase headroom and target reachability.

The selected Battery/Balanced/Loads preference adjusts only the conservative flexible-energy reservation. It cannot weaken electrical protection or override a definite forecast shortfall.

## Emergency battery charge from grid

v1.0 exposes:

- **Avvia ricarica di emergenza batteria**
- **Interrompi ricarica di emergenza batteria**

Because inverter controls differ between installations, Casa ES does not guess inverter services. In Options, configure two explicit Home Assistant `script.*` entities:

- start grid-charge script
- stop grid-charge script

The start script receives these variables:

- `power_w`
- `target_soc`
- `max_minutes`

Casa ES automatically requests the stop script when target SOC is reached, the timeout expires, or the integration unloads normally. The buttons remain unavailable until both scripts are configured.

## Advisory AI

Gemini / AI Task remains advisory. The AI receives:

- current measured electrical data
- deterministic policy
- selected energy preference
- forecast and weather context
- phase-load attribution
- managed-device runtime modes
- adaptive climate profiles

AI recommendations cannot override local deterministic guardrails.

## Diagnostics and tomorrow's validation

Download diagnostics from:

`Settings -> Devices & services -> Casa ES Energy Manager -> Download diagnostics`

The v1 diagnostics include:

- all mapped source sensors and live states
- forecast inputs and planner policy
- energy preference
- L1/L2/L3 headroom and phase attribution
- every managed-device configuration
- runtime AUTO/OVERRIDE/OFF mode
- real device power
- HVAC mode/action
- adaptive learned profile and sample counts
- admission estimate
- minimum ON/OFF state
- dry-run decision and reason
- emergency grid-charge state
- coordinator health

For the first sunny-day validation, useful diagnostics are:

1. shortly before meaningful PV production
2. when PV first becomes sufficient for a managed load
3. after one climate/variable load has been active for a while
4. around midday/high PV
5. whenever a decision looks wrong

Attach those diagnostics files to the development chat. Casa ES automatic appliance switching should remain disabled until the dry-run decisions have been reviewed against real behavior.

## Installation / update

Add this repository to HACS as a custom Integration repository:

`https://github.com/Sangua90/Casa-ES-Energy-Manager`

Install/update **Casa ES Energy Manager**, restart Home Assistant, then open:

`Settings -> Devices & services -> Casa ES Energy Manager`

Existing 0.4.x configuration remains readable; new v1 fields use safe defaults.

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
