# CivicSignal

**Traffic lights the city can steer.**

Several SUMO traffic simulations run side by side, with identical traffic. Each one
uses a different traffic-signal strategy:

- **fixed** — fixed-time, SUMO's default 90 s program
- **actuated** — detectors extend green
- **greenwave** — coordinated offsets along the major axis
- **adaptive** — CivicSignal, a weighted max-pressure controller

A browser dashboard shows the maps, live metrics (delay, stops, CO₂, fuel, NOx,
particulates, bus and ambulance trip times), and controls. The city steers CivicSignal
with four weights: **flow**, **climate**, **transit** and **fairness**. Emergency
vehicles get green corridors.

## Constraints

- 8 GB laptop. Python 3.12 venv. SUMO from pip (`eclipse-sumo`, `traci`, `sumolib`,
  all `1.28.0`), always **headless** (`sumo`, never `sumo-gui`).
- One FastAPI process serves the page and a websocket on `localhost:8000`.
- Frontend: plain HTML, CSS and JS with canvas. Chart.js 4.4.1 is downloaded into
  `web/vendor/`. No npm, no build step.
- Every Python module starts with `from __future__ import annotations`.

## Layout

```
core/types.py
core/controller.py
core/sumo_runner.py
scenario/build.py          # outputs into scenario/<id>/
compare.py
server.py
tests/
web/                       # index.html, app.js, style.css, scenarios/<id>.json
recordings/
results/
pitch/
```

## Exact dataclasses — `core/types.py`

```python
@dataclass
class Weights:
    flow: float = 1.0; climate: float = 0.0; transit: float = 0.0; fairness: float = 0.0; preempt: bool = True

@dataclass
class TLSInfo:
    tls_id: str; links: list[tuple[str, str]]; green_states: list[str]; initial_state: str   # links[i] = (in_lane, out_lane)

@dataclass
class LaneObs:
    halting: int = 0; co2_mg_s: float = 0.0; buses: int = 0; max_wait_s: float = 0.0
    emergency_dist_m: Optional[float] = None; approaching: int = 0; buses_moving: int = 0
```

## Frame protocol

The server sends one frame per sim step:

```json
{
  "type": "frame",
  "t": 0,
  "scenario": "grid",
  "sims": {
    "<strategy>": {
      "veh": [["id", "x", "y", "angle_deg", "kind", "halted"]],
      "sig": {"<lane>": "G|y|r"},
      "m": {"<metrics>": 0},
      "heat": {"<edge>": 0.0}
    }
  },
  "events": []
}
```

- Vehicle **kind**: `c` car, `b` bus, `e` ambulance, `x` crash.
- SUMO coordinates have **y pointing up**; **angles are clockwise from north**.
