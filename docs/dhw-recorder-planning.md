# DHW planning 1.5.20

The boiler remains powered. Automatic control never calls ON/OFF on its main
water-heater entity or on `switch.ariston_power`. GREEN remains the normal mode;
only the configured Boost switch is used for photovoltaic heat storage.

## History and planning

The coordinator reads up to 30 days of full Home Assistant recorder states at
startup and hourly, in the recorder executor. Temperature attributes must be
included; state-only history misses temperature changes while the mode stays
GREEN. UTC timestamps are aligned across temperature, heating, Boost and
legionella, then converted to the Home Assistant timezone for daily/hourly
aggregation. Celsius and Fahrenheit are handled explicitly. Invalid/non-finite
values, naive timestamps, unavailable states and heating transitions are not
treated as demand. Recorder timestamps are used, not the refresh polling time.

Coarse slow cooling is standby loss. Confirmed faster cooling is reduced by
estimated standby loss before being counted as equivalent temperature demand.
Positive heating is not converted into draw. Boost and legionella intervals
are excluded; starts of native heating never synthesize a shower. Temperature
alone cannot reveal all draws masked by simultaneous heating, stratification,
or Boost. This is an explicit lower-bound estimator, not a litre/flow meter.

Repeated draws in an hour are summed by local calendar date. Only completed,
sufficiently covered observed-demand days train the forecast. The current day
cannot dilute a later evening forecast. Same weekdays are preferred after two
observations; otherwise all available demand days are age weighted. Days with
no detected demand are omitted to avoid treating absence as reduced household
needs. The model stores only recorder-derived completed daily aggregates for
30 days in a separate Home Assistant storage file; it does not overwrite the
legacy thermal profile or modify the recorder database.

Without household history, explicit bootstrap demand is used (07:00 and 18:00)
and the diagnostic plan is marked `bootstrap`. Reserve starts at 4.5 degrees,
increases with positive historical forecast residuals, and is bounded at 8.
Targets include remaining demand and bounded standby loss, with normal,
configured hard and actual appliance maxima all applied. Insufficient thermal
capacity is reported rather than hidden by clipping the target.

Tomorrow's hourly demand and reserve are calculated every refresh from the
completed history. Poor tomorrow FV (less than 6 kWh or 35% of today's forecast)
also reserves tomorrow morning demand during today's solar opportunity.

## GREEN and Boost

GREEN start time uses the median measured heating rate below 53 C, plus 25%
time allowance and 30 minutes. GREEN is available regardless of FV or battery
SOC; the target is capped at 53 C, the manufacturer's heat-pump limit. Raising
a setpoint in GREEN does not force the compressor to start: the appliance's
own thermostat/protections remain authoritative. GREEN is selected only in
automatic operation, never during manual Boost or legionella.

Boost uses measured grid export, plus the solar-covered portion of an already
running heater; battery discharge/grid import are subtracted. Guarded PV
potential may be used near the battery target to harvest inverter clipping.
Existing battery allocation, minimum SOC, phase headroom and inverter limits
remain enforced. Solar start/continue thresholds have hysteresis and at least
five minutes OFF before a restart. A Boost target can increase during a cycle.
Only owned Boost can be stopped, returning the setpoint to the native base.

An Ariston Lydos Hybrid heat pump cannot provide a 60–65 C tank using GREEN
alone. If the predicted need exceeds 53 C without solar surplus, the plan
exposes the shortfall. Grid resistance requires an explicit, expiring
approval from the configured Home Assistant Companion phone notification.
It cannot promise that any number of showers fits in a finite tank.

## Actionable recovery notification (1.5.21)

Configure `thermal_notify_service` as a single `notify.mobile_app_*` service.
The controller asks before the predicted use when current surplus cannot
cover Boost and GREEN cannot reach the planned reserve target. Advance notice
is computed from the current temperature gap and the observed Boost heating
rate, with 25% extra heating time, 30 minutes heating margin and 45 minutes to
answer. The observed rate is the lower quartile of at least six valid positive
Boost temperature intervals, capped at 12 C/h; fallback is a conservative
4 C/h. Precharge target changes also recompute notification timing.

YES authorizes one recovery up to the requested temperature, also from grid,
while retaining electrical, main-power, manual-mode and legionella protections.
The time limit matches the estimated heating duration, at least 2 hours and
at most 24 hours. NO or no answer never authorizes grid resistance; independent
solar Boost continues under the existing surplus rules. Replies expire after
45 minutes and unique tokens prevent old or duplicate buttons from restarting
heating. Decisions are persisted and deduplicated for the requested usage day.
Owned grid Boost stops at target, expiration or electrical protection, returning
to the native base. Restart recovers owned grid Boost for safe cleanup. The
notification cannot guarantee timely comfort if the user replies late or
electrical protections prevent heating.

The sensor **Piano acqua calda** exposes per-boiler target, deadline, GREEN
start time, tomorrow's demand, reserve, bootstrap status and capacity shortfall.
Full reconstructed models and history errors are available in diagnostics.

## Validation and installation

Behaviour tests cover recorder alignment, invalid input, passive cooling,
heating starts, Boost/legionella exclusion, current-day exclusion, forecasting,
early GREEN, owned/manual Boost, battery/grid supply, phase protection and
main boiler power protection. Existing static version-contract failures also
occur on the original 1.5.19 repository; they are not runtime verification.

Local results: 23 new tests pass, including the 25-hour Europe/Rome autumn DST
day. GitHub's existing Home Assistant 2026.8.3 import/form runtime smoke and HACS
validation passed on the first patch. The recorder dependency is declared for
startup ordering and hassfest. The original 11 static failures are left visible
rather than changing unrelated climate policy or weakening historical tests.

Install the reviewed integration files using the existing HACS/custom integration
workflow and restart/reload Home Assistant. This source change alone does not
update an installation still running 1.5.18. Verify recorder retention, the
new sensor and measured temperature at the predicted usage deadline after
installation. No live boiler command is needed to review the source change.
