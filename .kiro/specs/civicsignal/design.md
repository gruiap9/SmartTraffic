# CivicSignal — Design

## Overview

One FastAPI process runs several headless SUMO simulations (one per strategy
pane), each over its own labelled TraCI connection, and broadcasts a frame per
step to a vanilla-JS canvas dashboard. The adaptive strategy is a weighted
max-pressure controller the city steers live.

## Architecture

```
scenario/build.py   ──>  scenario/<id>/ (net, routes, tls.add, sumocfg, meta)
                         web/scenarios/<id>.json (map geometry for the UI)

core/types.py        Weights, TLSInfo, LaneObs
core/controller.py   WeightedMaxPressure (register/step/terms/score/explain)
core/sumo_runner.py  SumoRunner — one headless SUMO per strategy over TraCI

compare.py           parallel benchmark -> results/compare_<map>.{md,csv}
server.py            FastAPI: websocket dashboard + /api/copilot + CLI replay
web/                 index.html, style.css, app.js, sidebar.js, vendor/
```

## Components

### Scenario builder
- Grid via `netgenerate`; Midtown via Overpass + `netconvert`, clipped to the
  rotated rectangle of four corner avenues, speeds set to 25 mph.
- Both emit `actuated` and `greenwave` TLS programs, demand flows, buses, an
  ambulance route, and a frontend map JSON. Routes are sorted by departure
  time (SUMO drops unsorted flows).

### SumoRunner
- Per-vehicle subscriptions; teleport sentinel (negative CO2) skipped.
- Per-lane `LaneObs`; split OSM approaches chained up to 250 m.
- Ambulance corridor via `getNextTLS`. Controller applied only on change.
- Emits `{veh, sig, m, heat}` per pane per step; cumulative metrics.

### WeightedMaxPressure
- Four-term score per green; hold / switch (beat current by > HYST) / rotate at
  max-green; preempt for ambulances through yellow + all-red. `explain()`
  returns mode, reason, and every option with served streets and signed terms.

### Server
- `Sim` holds panes, shared `Weights`, playing state, sps, pending events,
  inspected light, followed vehicle, history. Records every session to
  `recordings/`. Restarts guarded by a lock in a worker thread.
- `/api/copilot` reads `GEMINI_API_KEY` from `.env`, asks Gemini with a JSON
  schema, falling through models on 400/404/429/5xx.

### Frontend
- Per-pane `Transform` rotates by `rotate_deg`, frames the signalized area +
  95 m, and quarter-turns tall panes for a larger scale. Base layer (buildings,
  lanes, junctions, labels) drawn once offscreen; live layer each frame; heat
  layer every 500 ms on a shared scale.

## Testing
- Controller unit tests with a fake 4-link light (no SUMO).
- Benchmark provides end-to-end numeric validation across maps and seeds.
