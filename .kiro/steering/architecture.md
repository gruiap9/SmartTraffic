---
inclusion: fileMatch
fileMatchPattern: 'core/*.py'
---

# Core architecture

Context for work in `core/`. Three modules, each with a single job.

## `core/types.py`

Exact dataclasses — do not change their field names or defaults:

- `Weights(flow=1.0, climate=0.0, transit=0.0, fairness=0.0, preempt=True)`
- `TLSInfo(tls_id, links, green_states, initial_state)` where
  `links[i] = (in_lane, out_lane)` indexed by TLS link index.
- `LaneObs(halting, co2_mg_s, buses, max_wait_s, emergency_dist_m,
  approaching, buses_moving)`.

## `core/controller.py` — WeightedMaxPressure

Score of a green state `g` is four terms:

- Flow: per unique served `(in, out)` pair, `flow·(halting[in] − halting[out])`.
- Per served in-lane: `climate·approaching`, `transit·5·(buses + buses_moving)`,
  `fairness·max_wait/30`.

Timing: `MIN_GREEN 8`, `MAX_GREEN 60`, `YELLOW 3`, `ALL_RED 1`, `HYST 2.0`,
`PREEMPT_DIST_M 300`. Switch only when the best state beats the current by more
than `HYST`; rotate at max-green; preempt for ambulances within range (ignoring
min/max green but never skipping yellow). Every decision records a plain-English
reason surfaced by `explain()`.

`BALANCED = dict(flow=1, climate=1, transit=0.5, fairness=0, preempt=True)`.

## `core/sumo_runner.py` — SumoRunner

Drives one headless SUMO per strategy over its own TraCI connection. Subscribes
each vehicle to position/angle/type/speed/CO2/wait/lane/NOx/PM/fuel, skips the
negative-CO2 teleport sentinel, builds per-lane `LaneObs` (chaining split OSM
approaches up to 250 m), sets the ambulance corridor via `getNextTLS`, applies
the controller only when the state changes, and emits one frame per step.

The controller receives `step(tls_id, t, lanes)` where `lanes` is keyed by lane
id and covers both in- and out-lanes, so out-lane backpressure is available.
