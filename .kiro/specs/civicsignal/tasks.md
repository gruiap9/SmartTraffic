# Implementation Plan — CivicSignal

All tasks are complete; this plan was reconstructed from the shipped code. Each
task references the requirements it satisfies.

- [x] 1. Project brief and dataclasses
  - Capture the brief, constraints and frame contract in `docs/PROJECT.md`.
  - Implement `core/types.py` with `Weights`, `TLSInfo`, `LaneObs`.
  - _Requirements: 9.1, 9.2, 9.3_

- [x] 2. Environment and tooling
  - Create the Python 3.12 venv; install SUMO 1.28.0, FastAPI, pytest; vendor
    Chart.js 4.4.1; write `.gitignore`.
  - Verify `sumo --version` and `netgenerate --version`.
  - _Requirements: 9.1, 9.2_

- [x] 3. Grid scenario
  - Build the 3×3 grid, demand flows, bus flow, ambulance route, and the
    `actuated`/`greenwave` programs in `scenario/build.py`.
  - Verify exactly 9 controllable lights.
  - _Requirements: 1.1, 1.3, 2.1, 2.3, 2.4_

- [x] 4. Midtown scenario from OpenStreetMap
  - Download OSM via Overpass (clean User-Agent), run netconvert, clip to the
    rotated corner rectangle, set 25 mph, build demand/buses/ambulance/events
    and map extras.
  - Verify exactly 54 signals.
  - _Requirements: 1.3, 2.2, 2.3, 2.4_

- [x] 5. Simulation runner
  - Implement `core/sumo_runner.py`: multi-sim over labelled TraCI,
    subscriptions with teleport-sentinel skip, `LaneObs` with advance chaining,
    ambulance corridor, cumulative metrics and CO₂ heatmap, and the event
    methods.
  - _Requirements: 1.2, 1.4, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [x] 6. Controller and unit tests
  - Implement `core/controller.py` WeightedMaxPressure (scoring, switching,
    rotation, transitions, preemption, explainability, live weights).
  - Write `tests/test_controller.py` (fake 4-link light); verify all pass.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.2, 4.3, 4.4_

- [x] 7. Benchmark
  - Implement `compare.py`: parallel runs, ambulance dispatch schedule,
    tripinfo parsing (ambulances excluded), mean ± std tables (md + csv).
  - Run both maps at seeds 1–3 and record the numbers.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 9.4_

- [x] 8. FastAPI server
  - Implement `server.py`: `Sim`, the asyncio broadcast loop, websocket message
    handling, session recording, `/api/copilot`, and the headless `--record`
    CLI.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [x] 9. Dashboard maps and layout
  - Build `web/` control-room UI: top bar, 2/4 panes with canvas maps
    (rotation, fit, quarter-turn), signal bars, vehicles, heatmap, chart strip.
  - _Requirements: 8.1, 8.2, 9.2_

- [x] 10. Sidebar and features
  - Four-tab sidebar (Impact, Policy, Events, Inspect), follow-a-driver card,
    pop-outs, keyboard shortcuts, URL options, responsive layout.
  - _Requirements: 8.3_

- [x] 11. Verify, record and document
  - Run tests and the full benchmark; record `recordings/demo.jsonl`; write
    `README.md` and `pitch/script.md` using only numbers from `results/`.
  - Implement the `?replay=` client path.
  - _Requirements: 6.3, 9.4_
