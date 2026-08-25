# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Status: v0.3 alpha / advisory only.**  
> This release does **not** switch appliances, change inverter settings or start grid charging.

## Core monitoring

The integration reads:

- measured PV power from the inverter
- house/load power
- grid power (`positive = import`)
- battery SOC
- battery power (`positive = charging`)
- optional L1/L2/L3 load power sensors

It creates calculated entities for grid, inverter and per-phase headroom, battery charge/discharge power and manager status.

All power inputs may be in `W`, `kW` or `MW`; values are normalized to watts.

## Solar opportunity model

Casa ES uses a zero-export inverter. When the battery is full and house demand is low, measured PV power can fall because the inverter curtails the array. A low measured PV value therefore does **not** always mean that little solar energy is available.

Casa ES keeps two separate concepts:

- **Measured PV**: what the inverter is actually producing now.
- **Potential PV**: a forecast/simulated estimate of what the panels could produce if the inverter were not curtailing them.

The potential estimate never replaces a higher real measurement. The effective potential is always at least the measured PV value.

The integration calculates:

- estimated potential PV power
- estimated unused PV potential (`potential - measured`)
- estimated potential PV after current house load
- a conservative `Likely PV curtailment` flag when battery SOC is nearly full, grid import is near zero and the potential estimate is materially above measured production

`Likely PV curtailment` is only a heuristic. Forecast error can create the same pattern, so it is never used as an electrical safety signal.

### Solar forecast inputs

The following optional sensors can be mapped in integration options:

- potential PV power now (`W`/`kW`/`MW`)
- remaining PV energy today (`Wh`/`kWh`/`MWh`)
- current-hour forecast: **power or energy**
- next-hour forecast: **power or energy**
- total PV energy today (`Wh`/`kWh`/`MWh`)
- PV energy tomorrow (`Wh`/`kWh`/`MWh`)

Current-hour and next-hour fields intentionally accept both power and energy forecasts. Casa ES detects the sensor unit automatically and stores power as watts or energy as kWh. It never silently converts a power value such as `635 W` into `635 kWh` or `0.635 kWh`, because power and energy are different physical quantities.

Energy inputs are normalized to kWh. Power inputs are normalized to watts.

If the selected daily forecast sensor exposes a `watts` attribute containing a timestamp-to-power dictionary, Casa ES reads future forecast points and sends the curve to the planner. This is optional and provider-independent.

### Weather and extra context

An optional `weather.*` entity can be selected. When available, the AI planner requests the next six hourly forecast entries using Home Assistant's weather forecast service.

There is also an **Additional AI context sensors** selector. It accepts multiple arbitrary `sensor.*` entities, for example:

- solar irradiance / radiation
- outdoor temperature
- humidity
- wind speed
- rain rate
- cloud-related sensors
- any locally useful weather or energy signal

These additional sensors are context for planning only. They do not participate directly in electrical protection calculations.

## v0.3 deterministic planner policy and AI guardrails

Version 0.3 keeps Gemini as a planning advisor but no longer lets the model freely choose strategies that contradict measured electrical conditions.

Before every AI request, Casa ES calculates a deterministic policy including:

- battery energy still required to reach the configured target SOC
- hours remaining to the target time
- grid, inverter and minimum per-phase headroom
- electrical pressure (`normal`, `elevated`, `critical`)
- solar state (`absent`, `very_low`, `low`, `useful`, `high`)
- forecast energy to the target when the available power curve fully covers the target window
- forecast margin before base-house consumption
- whether a definite solar shortfall has been demonstrated
- whether `protect_grid`, `grid_charge`, `battery_first` or `use_surplus` are justified

The policy is passed to the AI as a **binding constraint**.

Important guardrails:

- `protect_grid` is rejected when grid, inverter and phase margins are normal.
- A critical local electrical condition forces `protect_grid` and disables flexible loads.
- `grid_charge` and the grid-charge recommendation are allowed only when Casa ES can demonstrate a forecast shortfall before the battery target.
- The model may not describe solar production as absent unless the deterministic solar state is actually `absent`.
- Raw AI strategy and final strategy are both exposed, together with the guardrail reason when a correction was applied.

These guardrails are advisory-layer constraints only. Future real device control will still pass through a separate deterministic safety engine.

## Advisory AI planner

The optional AI planner is queried every 30 minutes by default.
It uses Home Assistant's `AI Task` building block (`ai_task.generate_data`) so the model returns structured advice instead of directly controlling the house.

The planner can return:

- strategy (`battery_first`, `balanced`, `use_surplus`, `protect_grid`, `grid_charge`, `insufficient_data`)
- whether flexible loads are advisable
- whether grid charging should be considered
- suggested PV power to reserve for battery charging
- confidence
- a short reason in Italian

The planner receives measured PV, potential PV, solar forecast, optional forecast curve, weather forecast, three-phase margins and additional context sensors.

The AI recommendation is **advisory only**. Electrical safety, phase limits and all future device control remain deterministic and local.

### AI configuration

Configure a provider that exposes an `ai_task.*` entity in Home Assistant, for example Google Gemini.
Then open the Casa ES Energy Manager integration options and configure:

- Enable advisory AI planner
- AI Task entity
- interval (default 30 minutes)
- battery capacity (default 14.3 kWh)
- battery target SOC (default 100%)
- battery target hour (default 17:00)

A button named **Aggiorna strategia AI** can request a recommendation immediately.

## Diagnostics

Home Assistant can export a compact diagnostics file for the integration.
The file contains the information useful for Casa ES Energy Manager testing, including:

- configured source entity IDs and current states
- measured and potential PV inputs
- solar forecast sensor values and forecast curve when available
- weather and additional context sensor mappings
- load, grid, battery SOC and battery power
- L1/L2/L3 power values
- configured inverter, grid and phase limits
- deterministic planner policy and AI guardrail result
- AI context and latest raw/final advisory result
- all values calculated by the coordinator
- coordinator health and sign conventions

To download it:

`Settings -> Devices & services -> Casa ES Energy Manager -> Download diagnostics`

The diagnostics file is intended to be attached to the development chat when a calculation or sensor mapping needs to be checked. It does not include Home Assistant credentials, tokens or passwords.

## Why a separate domain?

The integration domain is `casa_es_energy_manager`, so this project can be installed alongside the original PV Excess Control integration during development and testing.

## Installation

Add this repository to HACS as a custom **Integration** repository:

`https://github.com/Sangua90/Casa-ES-Energy-Manager`

Then install **Casa ES Energy Manager**, restart Home Assistant, and add it from:

`Settings -> Devices & services -> Add integration -> Casa ES Energy Manager`

## Safety architecture

Casa ES Energy Manager is being developed in layers:

1. deterministic electrical monitoring and protection
2. solar opportunity and forecast model
3. advisory AI planning with deterministic guardrails
4. device priority and per-phase admission control
5. battery target scheduling and optional grid charging

Forecast and AI may influence future optimization decisions, but they will never bypass phase, grid, inverter, battery or anti-cycling safety rules.

## Origin and license

Casa ES Energy Manager is a modified/derived project based on
[InventoCasa/PV-Excess-Control](https://github.com/InventoCasa/PV-Excess-Control).

The fork was repurposed for Casa ES beginning **25 August 2026**. Original copyright notices and the GNU Affero General Public License v3 are retained. See `LICENSE` and `NOTICE.md`.
