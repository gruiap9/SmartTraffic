# Requirements — CivicSignal

## Introduction

CivicSignal runs several SUMO traffic simulations side by side on identical
traffic, each driven by a different signal-control strategy, and presents them
in a live browser dashboard. A city planner steers one strategy — a weighted
max-pressure controller — with four weights (flow, climate, transit, fairness)
and a preemption toggle, and watches delay, emissions, and bus/ambulance trip
times respond in real time. Emergency vehicles receive green corridors.

This spec was reverse-engineered from the existing implementation so the
requirements describe behaviour the code actually delivers.

## Glossary

- **Strategy**: a signal-control policy — `fixed`, `actuated`, `greenwave`, or
  `adaptive` (CivicSignal).
- **Pane**: one strategy's live map in the dashboard.
- **Controllable light**: a traffic light with at least 2 distinct green states.
- **Frame**: one broadcast per simulation step holding every pane's vehicles,
  signals, metrics, and heatmap.

## Requirements

### Requirement 1 — Compare strategies on identical traffic

**User story:** As a city planner, I want every strategy driven by the same
demand, so that any difference comes only from the control logic.

#### Acceptance criteria
1. WHEN a scenario runs THEN the system SHALL simulate any subset of `fixed`,
   `actuated`, `greenwave`, and `adaptive` on identical demand.
2. WHEN multiple strategies run in one process THEN each SHALL use its own
   labelled TraCI connection so the simulations stay independent.
3. THE grid scenario SHALL expose exactly 9 controllable lights; THE midtown
   scenario SHALL expose exactly 54.
4. WHEN a controller is attached THEN that light SHALL run program "0" and be
   driven by `setRedYellowGreenState`; otherwise it SHALL run the strategy's
   named program (`actuated` or `greenwave`) or "0" for `fixed`.

### Requirement 2 — Build two scenarios

**User story:** As a developer, I want reproducible maps, so that the benchmark
and dashboard have stable inputs.

#### Acceptance criteria
1. THE grid scenario SHALL be generated with `netgenerate` (3×3, 200 m, 2
   lanes), with demand flows across three periods, a bus flow, and an ambulance
   route.
2. THE midtown scenario SHALL be built from OpenStreetMap via Overpass and
   `netconvert`, clipped to the rotated rectangle of four named corner
   intersections (6th & 47th, 6th & 55th, 3rd & 55th, 3rd & 47th) grown 4%,
   with every kept edge set to 11.18 m/s.
3. EACH scenario SHALL write `actuated` and `greenwave` TLS programs, a
   `meta.json`, and a `web/scenarios/<id>.json` map description.
4. THE route file SHALL be sorted by departure time so SUMO does not drop
   flows.

### Requirement 3 — Weighted max-pressure controller

**User story:** As a planner, I want a controller I can steer live, so that
policy changes take effect without a re-timing study.

#### Acceptance criteria
1. THE controller SHALL score each candidate green state as the sum of: flow
   `flow·(halting[in] − halting[out])` per unique served (in,out) pair, plus
   per served in-lane `climate·approaching`, `transit·5·(buses + buses_moving)`,
   and `fairness·max_wait/30`.
2. THE controller SHALL hold the current green until MIN_GREEN (8 s) and
   SHALL switch only WHEN the best state beats the current by more than HYST
   (2.0).
3. WHEN a green reaches MAX_GREEN (60 s) THEN the controller SHALL rotate to
   the next-best state.
4. EVERY change SHALL pass through YELLOW (3 s) then ALL_RED (1 s); the yellow
   SHALL be 'y' exactly where the current state is green and the target is not.
5. WHEN weights change THEN the controller SHALL apply them on the next step.
6. THE controller SHALL expose `terms()`, `score()`, and `explain()` returning
   the mode, a plain-English reason, and every option with served street names,
   signed terms, total, and current/target flags.

### Requirement 4 — Emergency preemption

**User story:** As a dispatcher, I want ambulances to get a green corridor.

#### Acceptance criteria
1. WHEN preemption is enabled AND an ambulance is within PREEMPT_DIST_M (300 m)
   on a served in-lane THEN the controller SHALL target the green serving that
   lane (closest ambulance wins).
2. Preemption SHALL ignore MIN_GREEN and MAX_GREEN but SHALL NOT skip yellow.
3. WHEN the ambulance is already on the current green THEN the controller SHALL
   hold that green while it approaches.
4. WHEN preemption is disabled OR the ambulance is out of range THEN the
   controller SHALL behave as if no ambulance were present.

### Requirement 5 — Simulation runner and metrics

**User story:** As an analyst, I want faithful per-step metrics for each sim.

#### Acceptance criteria
1. THE runner SHALL subscribe each departed vehicle to position, angle, type,
   speed, CO₂, waiting time, lane, lane position, NOx, PM and fuel.
2. THE runner SHALL skip vehicles reporting a negative CO₂ teleport sentinel.
3. THE runner SHALL build per-lane `LaneObs` and chain split OSM approaches
   through non-signal nodes up to 250 m so advance segments report into the
   controlled lane.
4. THE runner SHALL set `emergency_dist_m` on in-lanes of lights ahead of each
   ambulance via `getNextTLS`.
5. THE runner SHALL emit cumulative metrics including delay (halted veh-s /
   (arrived + running)), stops (moving→halted transitions), CO₂, idle CO₂,
   fuel (mg/745000), NOx, PM, bus and ambulance trip times, and teleports.
6. WHEN `tripinfo` is requested THEN the runner SHALL add emissions output and
   write unfinished trips.
7. THE runner SHALL support `spawn_ambulance`, `surge` (deterministic via
   `random.Random(1000+k)` so ids/departures match across sims), `crash`,
   `inspect`, `trip`, and `close`.

### Requirement 6 — Benchmark

**User story:** As a judge, I want trustworthy, reproducible numbers.

#### Acceptance criteria
1. THE benchmark SHALL run every strategy × map × seed in parallel (≤ 4
   workers) and dispatch ambulances at t = 700, 900, 1100, 1300, 1500.
2. THE benchmark SHALL parse tripinfo, excluding ambulances from headline
   metrics, and compute mean ± std per strategy.
3. THE benchmark SHALL write `results/compare_<map>.md` and `.csv` with the
   best value bold, a "CivicSignal vs best baseline" column, a wins count, a
   vs-fixed line, and every run listed.
4. `--quick` SHALL run 1 seed to 900 s.

### Requirement 7 — Live dashboard server

**User story:** As a viewer, I want a synced live view of every strategy.

#### Acceptance criteria
1. THE server SHALL serve the page and a websocket on `localhost:8000`.
2. ONE asyncio loop SHALL step all panes and broadcast each frame to every
   connected window; stepping and restarts SHALL be guarded by a lock and run
   restarts in a worker thread.
3. ON connect THE server SHALL send state, then history, then the last frame.
4. THE server SHALL handle `weights`, `event`, `control`, `speed`, `config`,
   `inspect`, and `follow` messages, and record every session to
   `recordings/`.
5. THE server SHALL expose `POST /api/copilot` reading `GEMINI_API_KEY` from
   `.env` (tolerating spaces around `=`) and falling through a model list on
   400/404/429/5xx.
6. THE server SHALL support a headless `--record` CLI whose first output line
   is the state message.

### Requirement 8 — Dashboard UI

**User story:** As a user, I want to see and steer the simulations.

#### Acceptance criteria
1. THE UI SHALL render 2 or 4 panes with canvas maps rotated by `rotate_deg`,
   framed on the signalized area + 95 m, quarter-turning tall panes for a
   larger scale.
2. EACH frame SHALL draw signal bars at stop lines and vehicles as rotated
   rects (grey cars, red when halted, blue buses, red ambulances with a halo,
   orange crash cars); the heatmap SHALL redraw every 500 ms on a shared scale.
3. THE UI SHALL provide a four-tab sidebar (Impact, Policy, Events, Inspect), a
   follow-a-driver card, pop-outs (`?view=`), keyboard shortcuts, URL options
   (`?replay`, `?t`, `?tab`, `?heat`, `?follow`, `?ask`), and a responsive
   layout.

### Requirement 9 — Non-functional constraints

#### Acceptance criteria
1. Python 3.12; SUMO 1.28.0 from pip; **headless only**.
2. Frontend is plain HTML/CSS/JS with vendored Chart.js 4.4.1; **no build
   step**.
3. EVERY Python module SHALL begin with `from __future__ import annotations`.
4. All numbers in the README and pitch SHALL come from `results/`.
