# Design — CivicSignal

## Overview

One FastAPI process runs several headless SUMO simulations — one per strategy
pane — each over its own labelled TraCI connection, and broadcasts one frame
per step to a vanilla-JS canvas dashboard. The adaptive strategy is a weighted
max-pressure controller the city steers live. The benchmark reuses the same
runner and controller offline and writes mean ± std tables.

This design reflects the implementation as built.

## Architecture

```
scenario/build.py  ──>  scenario/<id>/  net.net.xml, routes.rou.xml,
                                         tls.add.xml, sim.sumocfg, meta.json
                   ──>  web/scenarios/<id>.json   (map geometry for the UI)

core/types.py          Weights, TLSInfo, LaneObs            (dataclasses)
core/controller.py     WeightedMaxPressure                  (policy)
core/sumo_runner.py    SumoRunner                           (one SUMO per pane)

compare.py             ProcessPoolExecutor benchmark  ──> results/compare_*.{md,csv}
server.py              FastAPI: Sim + asyncio broadcast loop + /api/copilot + CLI
web/                   index.html, style.css, app.js, sidebar.js, vendor/chart
```

## Data flow

```
build.py → scenario files + web map JSON
server.py Sim → N × SumoRunner.step() → frame {sims:{pane:{veh,sig,m,heat}}}
            → broadcast over websocket → app.js renders panes + chart
            → sidebar.js renders Impact/Policy/Events/Inspect
browser → weights/event/control/... → Sim → controllers / runners
```

## Components and interfaces

### core/types.py
Three dataclasses with fixed field names:
`Weights(flow, climate, transit, fairness, preempt)`,
`TLSInfo(tls_id, links, green_states, initial_state)` where
`links[i] = (in_lane, out_lane)` by TLS link index, and
`LaneObs(halting, co2_mg_s, buses, max_wait_s, emergency_dist_m, approaching,
buses_moving)`.

### scenario/build.py
Shared helpers (fringe detection, TLS program generation, XML/sumocfg writers,
web-scenario emitter) plus a grid builder and a midtown builder. Midtown
downloads OSM (clean token User-Agent to avoid Overpass 406), runs netconvert,
finds the four corner intersections by street name, clips edges inside the
grown rotated rectangle, and applies a 25 mph edge-diff in a second netconvert
pass. `tls_coord()` resolves a representative point for both plain nodes and
joined TLS clusters.

### core/controller.py — WeightedMaxPressure
`register(info)` precomputes served (in,out) pairs per green. `step(tls_id, t,
lanes)` scores every green (four terms), then holds / switches past hysteresis
/ rotates at max-green / preempts, driving a small state machine
(green → yellow → all_red → green). `explain()` exposes the decision and every
scored option. Weights are mutable via `set_weights`.

### core/sumo_runner.py — SumoRunner
Starts SUMO with `sumolib.checkBinary("sumo")` and a label; reads programs with
`getAllProgramLogics`; asserts `meta.expected_tls`. Precomputes lane chains
(static topology) so split OSM approaches feed the controlled lane's `LaneObs`.
Each step: subscribe new vehicles, account arrivals/teleports, aggregate obs,
set the ambulance corridor, apply the controller on change, update a per-edge
CO₂ EMA heatmap, and emit the frame.

### compare.py
`run_one(scenario, strategy, seed, end, amb_times)` runs in a worker process,
dispatches ambulances, and parses tripinfo (ambulances excluded). The driver
aggregates to mean ± std and writes markdown + CSV with bolded bests, a
vs-best-baseline column, wins, and a vs-fixed line.

### server.py
`Sim` holds panes, shared `Weights`, playing/sps, pending events, inspected
light, followed vehicle, and history. One `_sim_loop` steps via
`asyncio.to_thread` so SUMO never blocks the event loop, then broadcasts.
Restarts run under a lock in a worker thread. `/api/copilot` calls Gemini with
a JSON response schema and model fallthrough.

### web/
`Transform` per pane handles rotation, fit, and the optional quarter-turn. The
base layer (buildings, lanes, junctions, labels, landmarks) is drawn once
offscreen; the live layer each frame; the heat layer every 500 ms. `sidebar.js`
renders the four tabs, the ambulance race, the follow card, and the throttled
Impact panel (≤ every 400 ms, trailing update). `app.js` also implements the
`?replay=` playback path.

## Error handling

- Teleporting vehicles (negative CO₂ sentinel) are skipped from metrics.
- Fringe lights lacking `actuated`/`greenwave` programs stay on "0".
- The copilot falls through models on 4xx/5xx and returns `{error}` rather than
  crashing.
- Unsorted route files are corrected at build time (SUMO would drop flows).

## Testing strategy

- **Unit:** `tests/test_controller.py` — 13 cases on a fake 4-link light, no
  SUMO: heavy-queue win, min-green, hysteresis, yellow+all-red ordering,
  preemption rules, max-green rotation, live weights, out-lane backpressure,
  climate platoon protection.
- **Integration / numeric:** `compare.py` across 2 maps × 4 strategies × 3
  seeds provides end-to-end validation; results feed the README.
- **Frontend:** `node --check` plus logic harnesses over recorded frames
  (Transform, rolling average, sidebar rendering).
