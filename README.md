# Casa ES Energy Manager

Custom Home Assistant integration for the Casa ES photovoltaic, battery and three-phase energy system.

> **Status: v0.1 alpha / read-only.**  
> This release does **not** switch appliances, change inverter settings or start grid charging.

## v0.1 goal

The first milestone validates the sensor mapping and the electrical safety model before any automatic control is enabled.

It reads:

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

## Diagnostics

From v0.1.1, Home Assistant can export a compact diagnostics file for the integration.
The file contains only the information useful for Casa ES Energy Manager testing:

- configured source sensor entity IDs
- current source sensor states and units
- PV, load, grid, battery SOC and battery power
- L1/L2/L3 power values
- configured inverter, grid and phase limits
- safety margin
- all values calculated by the coordinator
- coordinator health and sign conventions

To download it:

`Settings -> Devices & services -> Casa ES Energy Manager -> Download diagnostics`

The diagnostics file is intended to be attached to the development chat when a calculation or sensor mapping needs to be checked. It does not include Home Assistant credentials, tokens or passwords.

## Why a separate domain?

The integration domain is `casa_es_energy_manager`, so this project can be installed alongside the original PV Excess Control integration during development and testing.

## Installation for development

Add this repository to HACS as a custom **Integration** repository after the v0.1 branch has been merged into `main`.

Then install **Casa ES Energy Manager**, restart Home Assistant, and add it from:

`Settings -> Devices & services -> Add integration -> Casa ES Energy Manager`

## Safety

v0.1 is intentionally read-only. Later releases will add load priority, per-phase load admission, battery target scheduling and optional grid charging only after the calculated values have been validated against the real installation.

## Origin and license

Casa ES Energy Manager is a modified/derived project based on
[InventoCasa/PV-Excess-Control](https://github.com/InventoCasa/PV-Excess-Control).

The fork was repurposed for Casa ES beginning **25 August 2026**. Original copyright notices and the GNU Affero General Public License v3 are retained. See `LICENSE` and `NOTICE.md`.
