---
inclusion: always
---

# CivicSignal — project rules

These constraints apply to every change in this repository. They mirror the
brief in `docs/PROJECT.md` and must not be violated.

## Environment

- Python 3.12 only, inside the `.venv` virtual environment.
- SUMO comes from pip (`eclipse-sumo`, `traci`, `sumolib`, all `1.28.0`).
- SUMO is **always headless** — use the `sumo` binary, never `sumo-gui`.
- Target an 8 GB laptop. Keep memory and parallelism modest (benchmark uses
  at most 4 worker processes).

## Python

- Every Python module starts with `from __future__ import annotations`.
- Resolve SUMO binaries through `sumolib.checkBinary(...)` so they are found
  inside the venv regardless of PATH.
- Use the labelled TraCI connection object everywhere (`traci.getConnection`)
  so several simulations can coexist in one process.

## Frontend

- Plain HTML, CSS and JS with canvas. **No npm, no build step.**
- Chart.js 4.4.1 is vendored at `web/vendor/chart.umd.min.js`.
- When editing `web/style.css`, bump the `?v=N` query on the stylesheet link
  in `web/index.html` so browsers load the change on a normal refresh.

## Server

- One FastAPI process serves the page and a websocket on `localhost:8000`.
- One asyncio loop steps all panes and broadcasts each frame so every
  connected window stays in sync.

## Data contract

- Frame shape is fixed:
  `{"type":"frame","t":..,"scenario":..,"sims":{strategy:{"veh":[[id,x,y,angle_deg,kind,halted]],"sig":{lane:"G|y|r"},"m":{metrics},"heat":{edge:value}}},"events":[...]}`
- Vehicle kind letters: `c` car, `b` bus, `e` ambulance, `x` crash.
- SUMO coordinates have y pointing up; angles are clockwise from north.

## Layout

Keep the module layout: `core/`, `scenario/` (outputs into `scenario/<id>/`),
`compare.py`, `server.py`, `tests/`, `web/`, `recordings/`, `results/`,
`pitch/`.

## Verification

- Run `pytest` after controller or runner changes.
- A command exiting 0 is not proof of success — check the actual output
  (vehicles inserted, metrics differ, files written).
