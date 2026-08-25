# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Status: v0.2 alpha / advisory only.**  
> This release does **not** switch appliances, change inverter settings or start grid charging.

## Core monitoring

The integration reads:

- PV power
- house/load power
- grid power (`positive = import`)
- battery SOC
- battery power (`positive = charging`)
- optional L1/L2/L3 load power sensors

It creates calculated entities for:

- PV power remaining after measured house load
- grid import and grid headroom
- inverter headroom
- battery charge/discharge power
- L1/L2/L3 headroom
- warnings for grid, phase and inverter limits
- manager status

All power inputs may be in `W`, `kW` or `MW`; values are normalized to watts.

## v0.2 advisory AI planner

Version 0.2 adds an optional AI planner designed to be queried every 30 minutes.
It uses Home Assistant's `AI Task` building block (`ai_task.generate_data`) so the model returns structured advice instead of directly controlling the house.

The planner can return:

- strategy (`battery_first`, `balanced`, `use_surplus`, `protect_grid`, `grid_charge`, `insufficient_data`)
- whether flexible loads are advisable
- whether grid charging should be considered
- suggested PV power to reserve for battery charging
- confidence
- a short reason in Italian

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

The current planner deliberately does not invent missing forecast data. Solar forecast support will be added as a separate input so the AI can reason about the remaining production toward the battery target.

## Diagnostics

Home Assistant can export a compact diagnostics file for the integration.
The file contains the information useful for Casa ES Energy Manager testing, including:

- configured source entity IDs and current states
- PV, load, grid, battery SOC and battery power
- L1/L2/L3 power values
- configured inverter, grid and phase limits
- AI planner configuration and latest advisory result
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
2. advisory AI planning
3. device priority and per-phase admission control
4. battery target scheduling and optional grid charging

The AI layer will never bypass phase, grid, inverter, battery or anti-cycling safety rules.

## Origin and license

Casa ES Energy Manager is a modified/derived project based on
[InventoCasa/PV-Excess-Control](https://github.com/InventoCasa/PV-Excess-Control).

The fork was repurposed for Casa ES beginning **25 August 2026**. Original copyright notices and the GNU Affero General Public License v3 are retained. See `LICENSE` and `NOTICE.md`.
