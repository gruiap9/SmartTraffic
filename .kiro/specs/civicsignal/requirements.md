# CivicSignal — Requirements

## Introduction

CivicSignal runs several SUMO traffic simulations side by side on identical
traffic, each using a different signal strategy, and presents them in a live
browser dashboard. A city planner steers one strategy — a weighted max-pressure
controller — with four weights (flow, climate, transit, fairness) and watches
delay, emissions, and bus/ambulance trip times respond in real time. Emergency
vehicles get green corridors.

## Requirements

### Requirement 1 — Multiple strategies on identical traffic

**User story:** As a city planner, I want to compare signal strategies on the
same demand, so that differences come only from the control logic.

#### Acceptance criteria
1. WHEN a scenario runs THEN the system SHALL simulate `fixed`, `actuated`,
   `greenwave`, and `adaptive` (CivicSignal) on identical demand.
2. WHEN several strategies run THEN each SHALL use its own labelled TraCI
   connection so simulations stay independent in one process.
3. THE grid scenario SHALL have exactly 9 controllable lights; THE midtown
   scenario SHALL have exactly 54.

### Requirement 2 — Steerable adaptive controller

**User story:** As a planner, I want to steer the adaptive controller live, so
that policy changes take effect without re-timing studies.

#### Acceptance criteria
1. THE controller SHALL score each candidate green with four weighted terms:
   flow `flow·(halting_in − halting_out)`, `climate·approaching`,
   `transit·5·buses`, and `fairness·wait/30`.
2. THE controller SHALL honour min-green, max-green, hysteresis, yellow and
   all-red on every transition.
3. WHEN weights change THEN the controller SHALL apply them live.
4. THE controller SHALL expose a plain-English reason for every decision.

### Requirement 3 — Emergency preemption

**User story:** As a dispatcher, I want ambulances to get a green corridor.

#### Acceptance criteria
1. WHEN an ambulance is within 300 m of a light on its route THEN the
   controller SHALL target the green serving its lane.
2. Preemption SHALL ignore min/max green but SHALL NOT skip yellow.
3. WHEN preemption is disabled or the ambulance is out of range THEN the
   controller SHALL behave normally.

### Requirement 4 — Live dashboard

**User story:** As a viewer, I want a synced live view of every strategy.

#### Acceptance criteria
1. THE server SHALL serve the page and a websocket on `localhost:8000`.
2. One asyncio loop SHALL step all panes and broadcast each frame so all
   windows stay in sync.
3. THE dashboard SHALL render maps, signal states, vehicles, a CO2 heatmap, a
   chart strip, and a four-tab sidebar (Impact, Policy, Events, Inspect).

### Requirement 5 — Reproducible benchmark

**User story:** As a judge, I want trustworthy, reproducible numbers.

#### Acceptance criteria
1. THE benchmark SHALL run every strategy × map × seed in parallel and parse
   tripinfo, excluding ambulances from headline metrics.
2. THE benchmark SHALL write mean ± std tables (markdown + CSV) to `results/`.
3. All numbers in the README and pitch SHALL come from `results/`.

### Requirement 6 — Constraints

#### Acceptance criteria
1. Python 3.12; SUMO 1.28.0 from pip; headless only.
2. Frontend is plain HTML/CSS/JS with vendored Chart.js 4.4.1; no build step.
3. Every Python module SHALL begin with `from __future__ import annotations`.
