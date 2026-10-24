# CivicSignal — Tasks

- [x] 1. Project brief
  - Capture the brief, constraints, dataclasses and frame protocol in
    `docs/PROJECT.md`.
  - _Requirements: 6_

- [x] 2. Setup and smoke test
  - Create the Python 3.12 venv; install SUMO 1.28.0 + FastAPI + pytest;
    vendor Chart.js 4.4.1; write `.gitignore`.
  - Verify `sumo --version` and `netgenerate --version`.
  - _Requirements: 6_

- [x] 3. Grid scenario
  - `scenario/build.py` with shared helpers and the grid scenario.
  - Check: 9 controllable lights.
  - _Requirements: 1_

- [x] 4. Midtown Manhattan from OpenStreetMap
  - Download OSM via Overpass, netconvert, clip to the rotated rectangle,
    set 25 mph, build demand/buses/ambulance/events and map extras.
  - Check: exactly 54 signals.
  - _Requirements: 1_

- [x] 5. Simulation runner
  - `core/sumo_runner.py`: multi-sim over labelled TraCI, subscriptions,
    LaneObs with advance chaining, ambulance corridor, metrics, heat, events.
  - _Requirements: 1, 3_

- [x] 6. Controller and tests
  - `core/controller.py` WeightedMaxPressure; pytest with a fake 4-link light.
  - Check: all controller tests pass.
  - _Requirements: 2, 3_

- [x] 7. Benchmark
  - `compare.py` parallel runner; write `results/compare_<map>.{md,csv}`.
  - _Requirements: 5_

- [x] 8. Server
  - `server.py` FastAPI: Sim, asyncio broadcast loop, recording, copilot,
    headless replay CLI.
  - _Requirements: 4_

- [x] 9. Dashboard, maps and layout
  - `web/` control-room UI: top bar, panes with canvas maps, chart strip.
  - _Requirements: 4_

- [x] 10. Dashboard, sidebar and features
  - Four-tab sidebar, follow-a-driver, pop-outs, keys, URL options, responsive.
  - _Requirements: 4_

- [x] 11. Verify, record and document
  - Run tests + full benchmark; record `recordings/demo.jsonl`; write
    `README.md` and `pitch/script.md` using only numbers from `results/`.
  - _Requirements: 5_
