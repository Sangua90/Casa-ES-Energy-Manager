# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Version 1.2.0 — guarded autonomous control**  
> Electrical decisions remain deterministic and local. AI is advisory only. v1.2.0 can physically manage configured appliances, but the master **Controllo automatico reale** switch is intentionally **OFF by default** after installation/update and must be enabled explicitly by the user.

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
- **Bilanciata**: compromise between battery completion and flexible loads.
- **Carichi prioritari**: favors immediate use of available PV while preserving hard safety limits.

Measured grid, phase and inverter limits always override the selected preference.

## Managed devices

Managed devices are configured separately from the initial wizard. Important fields include:

- Home Assistant entity to control
- optional real power sensor
- device type: generic or **Climatizzatore / pompa di calore**
- initial nominal/fallback power estimate
- adaptive real-power learning
- numeric priority `1..100` (`1` = highest)
- physical phase L1/L2/L3/three-phase
- optional expected cycle/runtime
- optional minimum ON/OFF times
- minimum battery SOC
- grid integration permission and per-device limit
- daily runtime/activation limits
- allowed time window
- on-only/non-interruptible behavior
- battery-discharge limit while the device is active

The configured nominal power is an initial and fallback value. When a dedicated real power sensor is available and adaptive learning is enabled, Casa ES continuously learns the real behavior and uses the learned estimate for admission decisions.

### Climatizzatori / pompe di calore

A managed climate/PDC keeps multiple learned profiles inside the **same device**, for example:

- `cool`
- `heat`
- `dry`
- `fan_only`

If the managed entity itself is `climate.*`, the same entity can be used as mode reference.

If the real command is a `switch.*` — for example a switch controlling several indoor units on one outdoor machine — select one related `climate.*` as **Climate di riferimento per la modalità**. Casa ES continues to control the switch and reads the climate entity only to identify the current mode.

Existing valid v1.1.1 switch profiles are retained as a conservative bridge while the new per-mode buckets collect enough samples.

### Automatico / Manuale / Spento

Every managed device exposes its own mode selector:

- **Automatico**: Casa ES may start/stop the device when real control is enabled.
- **Manuale**: Casa ES observes and can continue learning, but never sends commands to that device.
- **Spento**: the device is excluded from automatic optimization; when real control is enabled, an active entity can be switched off.

## Master real-control switch

v1.2.0 adds **Controllo automatico reale**.

It defaults to **OFF**. With the master OFF, Casa ES continues calculating decisions and learning but sends no physical appliance commands.

When the master is ON:

- only devices in **Automatico** can be controlled normally;
- **Manuale** is never touched;
- hard measured electrical safety has first priority;
- minimum ON/OFF constraints are respected for normal energy decisions;
- a hard grid/phase/inverter protection may stop a flexible load immediately;
- at most one appliance command is sent per coordinator refresh so measurements can settle before another action.

Diagnostics include the master state, last real command, entity, reason, timestamp and any service error.

## Shared power meters

A shared meter is treated conservatively.

- It is not used for adaptive per-device learning because its watts cannot be attributed safely to one child load.
- Individual ON/OFF state remains authoritative for identifying which child device is active.
- The same meter is counted only once in phase attribution.
- If multiple active children behind one shared meter cannot be assigned safely to a single phase, Casa ES leaves that power in the measured phase's `other load` rather than guessing.

Measured L1/L2/L3 totals remain authoritative for safety at all times.

## Adaptive variable-load learning

Adaptive learning is persistent and continuous. `ready` means only that enough active samples exist to use the learned estimate; learning continues during future operation.

Standby/off watts do not become active samples. A climate/PDC learns separate mode profiles. Mature estimates are deliberately robust against isolated extreme spikes so a one-off meter anomaly does not permanently inflate admission power.

If learning is disabled, the meter is shared/unavailable, or a mode profile is not mature enough, Casa ES falls back safely to the configured nominal estimate or a previously mature conservative profile.

## Anti-cycling and automatic stops

Optional minimum ON and minimum OFF values are enforced for normal automatic behavior.

Automatic stops can be requested for conditions such as:

- minimum SOC violated
- configured battery-discharge limit exceeded
- grid import beyond the device tolerance
- daily runtime/activation limit reached
- outside allowed time window
- definite battery-target shortfall
- battery-first reserve becoming tight

An `on_only`/non-interruptible cycle resists ordinary energy-based stops, while hard electrical protection remains authoritative.

## Read-only monitored loads

Appliances that Casa ES must never control can be added as **Monitored loads** with name, real power sensor, phase and enabled state. They explain parts of measured phase consumption but are never added on top of the real phase totals.

## Solar and battery planning

Casa ES separates measured PV power, potential/unconstrained PV and future forecast energy. Potential PV and forecast improve planning but never replace measured electrical safety values.

## Emergency battery charge from grid

Casa ES exposes start/stop emergency charge buttons, but inverter-specific behavior remains explicit. Configure two Home Assistant `script.*` entities in Options if this feature is used.

The start script receives:

- `power_w`
- `target_soc`
- `max_minutes`

Casa ES requests the stop script when target SOC is reached, timeout expires, or electrical protection requires it. The buttons remain unavailable until both scripts are configured.

## Advisory AI

Gemini / AI Task remains advisory. AI recommendations cannot override deterministic local guardrails.

## Diagnostics

Download diagnostics from:

`Settings -> Devices & services -> Casa ES Energy Manager -> Download diagnostics`

Useful fields include source sensors, forecast inputs, planner policy, phase headroom/attribution, managed-device configuration, runtime modes, real power, adaptive profiles, dry-run decisions and real-control command history.

## Safe update path to v1.2.0

After updating and restarting Home Assistant:

1. leave **Controllo automatico reale** OFF;
2. reconfigure climate/PDC managed devices and select the appropriate climate mode reference;
3. verify managed-device settings and modes;
4. put the devices you want managed in **Automatico**;
5. enable **Controllo automatico reale** only when ready.

Turning the master OFF immediately prevents new Casa ES appliance commands while monitoring and learning continue.

## Installation / update

Add this repository to HACS as a custom Integration repository, install/update **Casa ES Energy Manager**, restart Home Assistant, then open:

`Settings -> Devices & services -> Casa ES Energy Manager`

Existing v1.0/v1.1 configurations remain readable. Obsolete wallbox/EV/dependency keys are ignored and removed when a managed device is reconfigured.

## Safety architecture

Safety priority is always:

1. measured grid / phase / inverter limits
2. deterministic local protection
3. battery target and user-selected energy preference
4. managed-device constraints and anti-cycling
5. forecast optimization
6. advisory AI

Forecast and AI never bypass electrical protection.

## Origin and license

Casa ES Energy Manager is a modified/derived project based on `InventoCasa/PV-Excess-Control` and retains GNU Affero General Public License v3 requirements and notices. See `LICENSE` and `NOTICE.md`.
