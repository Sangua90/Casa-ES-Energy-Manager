# House climate and Engine rollout

Integration 1.5.22 adds one house profile and separate room profiles. Their
`enabled` and `reviewed` fields initially prevent new physical climate commands.
Existing DHW planning, manufacturer anti-legionella and grid-recovery consent are
retained. Seasonal Ariston shutdown is deliberately pending hydraulic validation.

Radiator heating offers Automatico/Spento. Three machine controls (Salotto,
Ester, P1) offer Automatico/Manuale/Spento. P1 shares one compatible heating or
cooling mode. Manual operation is observed without automatic changes.

Before enabling gas control, verify actual valve `hvac_action=heating`, opening
delay, minimum hydraulic flow and post-stop dissipation. Unknown/unavailable valves
never count as open. A strong demand in one zone may qualify only after explicit
hydraulic validation; otherwise no automatic gas start is authorized. Weak demand
requires two distinct room profiles. Stop the boiler before lowering valve targets
and preserve the circuit during dissipation. These software checks do not replace
the boiler manufacturer's hydraulic protections.

Electrical starts require a known phase and inverter/phase headroom. New machine
starts are separated by two minutes; compressor mode cycles retain twenty-minute
minimum dwell except safety stops. Measured export and guarded curtailed FV are
both considered. Battery allocation continues to cap the available thermal budget.

The Engine never sends arbitrary services/entity commands. A bounded, expiring
versioned response can adapt preheat times only within configured limits. If the
Engine is unavailable, local schedule planning and protections continue.

Status is NORMAL or DEGRADED with specific reasons; unverified configurations stay
in observation. No historical COP or sensor-bias calibration is claimed. Model
rates use sufficiently long consistent heating/off intervals; manual heat pumps
and open/unknown windows mark samples contaminated. Only derived models and
significant decisions are persisted, at most every five minutes.

