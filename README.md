# CivicSignal

**Traffic lights the city can steer.**

CivicSignal runs several SUMO traffic simulations side by side on the same
traffic, each using a different signal strategy, and shows them in a live
browser dashboard. A city planner steers one of those strategies — a weighted
max-pressure controller — with four sliders (flow, climate, transit, fairness)
and watches delay, emissions, and bus and ambulance trip times respond in real
time. Emergency vehicles get green corridors.

The four strategies, all on identical demand:

- **fixed** — SUMO's default 90 s fixed-time program
- **actuated** — detectors extend green
- **green wave** — coordinated offsets along the major axis
- **CivicSignal** — a weighted max-pressure controller you steer live

There are two maps: a synthetic 3×3 **grid**, and **Midtown Manhattan** built
from OpenStreetMap (6th Ave to 3rd Ave, W 47th to W 55th; 54 signals).

---

## Results

Every strategy was run on every map at seeds 1, 2, 3 (full 2400 s rush-hour
day), with ambulances dispatched at t = 700, 900, 1100, 1300, 1500. Headline
metrics exclude ambulances. Numbers below come straight from
`results/compare_*.csv`.

### Midtown Manhattan — CivicSignal wins 10 of 12 metrics

Mean ± std across 3 seeds. Lower is better except Trips.

| Metric | fixed | actuated | green wave | **CivicSignal** |
|---|---|---|---|---|
| Delay (s) | 133.5 ± 2.0 | 19.9 ± 1.0 | 176.3 ± 0.8 | **19.6 ± 0.1** |
| Time loss (s) | 171.6 ± 2.5 | 46.7 ± 1.4 | 218.1 ± 0.8 | **39.7 ± 0.1** |
| Stops | 5.41 ± 0.06 | 3.16 ± 0.12 | 6.07 ± 0.13 | **2.16 ± 0.04** |
| CO₂ (kg) | 1472.8 ± 40.3 | 868.5 ± 25.0 | 1706.6 ± 32.8 | **771.5 ± 15.4** |
| Idle CO₂ (kg) | 653.2 ± 22.1 | 94.3 ± 6.7 | 861.8 ± 20.3 | **92.9 ± 1.7** |
| Fuel ($) | 592.39 ± 16.22 | 349.31 ± 10.04 | 686.44 ± 13.20 | **310.30 ± 6.19** |
| NOx (g) | 547.9 ± 14.8 | 292.8 ± 8.6 | 641.2 ± 11.6 | **264.4 ± 5.2** |
| PM (g) | 66.84 ± 1.09 | 59.90 ± 1.21 | 67.49 ± 0.38 | **57.81 ± 1.10** |
| Bus trip (s) | 281.2 ± 7.9 | 120.1 ± 4.5 | 306.7 ± 10.2 | **93.9 ± 2.4** |
| Amb trip (s) | 530.0 ± 29.7 | 235.3 ± 4.4 | 587.3 ± 6.0 | **149.3 ± 4.0** |
| Trips | 3205 ± 66 | 3205 ± 66 | 3202 ± 65 | 3205 ± 66 |
| Teleports | 1 ± 1 | 0 ± 0 | 17 ± 1 | 0 ± 0 |

**vs fixed:** delay −85.3%, CO₂ −47.6%.
CivicSignal ties on completed trips (everyone finishes) and ties actuated on
teleports (both 0).

#### Every Midtown run

| Strategy | Seed | Trips | Delay | CO₂ kg | Fuel $ | NOx g | PM g | Amb s | Bus s | Tele |
|---|---|---|---|---|---|---|---|---|---|---|
| fixed | 1 | 3247 | 133.8 | 1492.7 | 600.39 | 554.5 | 67.42 | 499.2 | 273.4 | 2 |
| fixed | 2 | 3240 | 135.2 | 1499.4 | 603.07 | 558.3 | 67.52 | 558.4 | 289.2 | 1 |
| fixed | 3 | 3129 | 131.4 | 1426.4 | 573.72 | 531.0 | 65.58 | 532.4 | 281.1 | 1 |
| actuated | 1 | 3247 | 20.6 | 887.2 | 356.80 | 299.5 | 61.02 | 240.2 | 125.3 | 0 |
| actuated | 2 | 3240 | 20.4 | 878.3 | 353.22 | 295.9 | 60.07 | 234.0 | 117.3 | 0 |
| actuated | 3 | 3129 | 18.8 | 840.2 | 337.90 | 283.1 | 58.62 | 231.6 | 117.7 | 0 |
| green wave | 1 | 3246 | 177.2 | 1727.7 | 694.92 | 649.0 | 67.85 | 593.2 | 298.8 | 17 |
| green wave | 2 | 3232 | 176.1 | 1723.3 | 693.18 | 646.7 | 67.53 | 581.2 | 303.0 | 17 |
| green wave | 3 | 3127 | 175.6 | 1668.8 | 671.23 | 627.8 | 67.09 | 587.4 | 318.2 | 18 |
| CivicSignal | 1 | 3247 | 19.6 | 782.9 | 314.88 | 268.2 | 58.67 | 153.6 | 96.3 | 0 |
| CivicSignal | 2 | 3240 | 19.4 | 777.6 | 312.76 | 266.5 | 58.19 | 148.6 | 93.8 | 0 |
| CivicSignal | 3 | 3129 | 19.6 | 754.0 | 303.26 | 258.4 | 56.57 | 145.8 | 91.5 | 0 |

### Grid — where CivicSignal loses

On the synthetic 3×3 grid, **SUMO's actuated program is the one to beat.**
Isolated intersections with detectors are exactly what gap-out actuation is
built for, and it wins delay, time loss, CO₂, fuel and NOx. CivicSignal is a
clear second and still crushes the fixed-time and green-wave baselines.

Mean ± std across 3 seeds:

| Metric | fixed | **actuated** | green wave | CivicSignal |
|---|---|---|---|---|
| Delay (s) | 29.9 ± 0.5 | **6.1 ± 0.2** | 46.5 ± 1.1 | 10.5 ± 0.1 |
| CO₂ (kg) | 560.4 ± 2.4 | **484.7 ± 3.6** | 678.7 ± 3.8 | 491.1 ± 1.8 |
| Fuel ($) | 225.39 ± 0.95 | **194.95 ± 1.44** | 272.99 ± 1.51 | 197.54 ± 0.74 |
| Bus trip (s) | 105.2 ± 3.4 | 78.6 ± 2.2 | 84.4 ± 2.8 | **73.0 ± 1.3** |
| Amb trip (s) | 113.7 ± 0.8 | 105.7 ± 2.7 | 133.7 ± 1.2 | **76.4 ± 1.3** |

So on the grid, **CivicSignal loses the throughput metrics to actuated**
(10.5 s vs 6.1 s delay; 491.1 vs 484.7 kg CO₂) — but it still wins the two
things the baselines can't do structurally: **bus trip time (73.0 s, best of
all four)** and **ambulance trip time (76.4 s, −27.7% vs the best baseline)**,
through transit weighting and green-corridor preemption. vs fixed it cuts
delay −64.8% and CO₂ −12.4%.

The honest read: on a toy grid a well-tuned actuated controller is hard to
beat on pure throughput. On a real, congested Manhattan network — where
pressure propagates across coupled intersections — CivicSignal dominates.

Full tables and every run: `results/compare_grid.md`,
`results/compare_midtown.md` and the matching `.csv` files.

---

## The dashboard

A dark "control-room" UI at `http://localhost:8000`:

- **Panes** (2 or 4) render each strategy's map on canvas, rotated so avenues
  run vertically, with buildings, lanes, junctions, street labels and
  landmarks drawn once offscreen. Each frame draws signal bars at every stop
  line and vehicles as rotated rects — grey cars (red when stopped), blue
  buses, red ambulances with a pulsing halo, orange crash cars.
- **Top bar**: map picker, compare picker (one baseline vs CivicSignal, or all
  four), a rush-hour pill, the clock, play/pause, reset, a speed slider
  (0.5×–10×), a 🔥 heatmap toggle, and a connection dot.
- **Chart strip**: cumulative idling CO₂ (solid) and a 90 s rolling-average
  queue (dashed) per pane, with the rush hour shaded.
- **Impact tab**: "Since start" vs "Last 2 min" deltas, hero tiles comparing
  CivicSignal to the baseline, a starred scoreboard, and a back-of-envelope
  yearly estimate (2 rush hours × 250 weekdays).
- **Policy tab**: five presets, four 0–3 sliders, a preemption toggle, and a
  **city co-pilot** that turns a plain-English goal into weights via Gemini.
- **Events tab**: 🚑 ambulance, 🎭 surge, 💥 crash, an ambulance race with a
  bar per pane, and an event log.
- **Inspect tab**: click any light — for CivicSignal you see the decision
  reason and per-option term bars; for the baselines, how they work and when
  they switch next.
- **Follow a driver**: click a car to track the same departure across every
  pane. **Pop-outs** (`?view=<tab>`), **keyboard** (Space, A, S, C, H, Esc),
  and **URL options** (`?replay=`, `?t=`, `?tab=`, `?heat`, `?follow=`,
  `?ask=`).

---

## Running it

Requires Python 3.12 and ~2 GB free RAM. SUMO comes from pip (headless only).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build the two scenarios (midtown downloads OSM once, then is committed)
python scenario/build.py grid
python scenario/build.py midtown

# Run the server, then open http://localhost:8000
python server.py
```

Other commands:

```bash
# Controller unit tests (no SUMO needed)
pytest

# Benchmark (parallel, 4 workers)
python compare.py --scenario all --seeds 1 2 3
python compare.py --scenario grid --quick       # 1 seed, 900 s

# Replay the committed demo in the browser
#   http://localhost:8000/?replay=/recordings/demo.jsonl&t=900&autoplay
```

The city co-pilot needs a `GEMINI_API_KEY` in a `.env` file. Without it, every
other feature works and the Policy tab's co-pilot box simply reports it is
unavailable.

---

## How it works

- **`scenario/build.py`** generates each map. Grid uses `netgenerate`; Midtown
  pulls roads, buildings and parks from Overpass, runs `netconvert`, clips to
  the rotated rectangle of the four corner avenues, and sets 25 mph. Both write
  `actuated` and `greenwave` TLS programs, demand flows, buses, an ambulance
  route, and a frontend map JSON.
- **`core/sumo_runner.py`** drives one headless SUMO per strategy over its own
  TraCI connection, so several sims coexist in one process. It subscribes each
  vehicle to position/emissions/waiting, builds per-lane observations (chaining
  split OSM approaches up to 250 m), tracks the ambulance corridor, applies the
  controller, and emits one frame of vehicles, signals, metrics and a CO₂
  heatmap per step.
- **`core/controller.py`** is the weighted max-pressure policy. Each candidate
  green scores four terms: flow `flow·(halting_in − halting_out)` per served
  turn, plus per in-lane `climate·approaching`, `transit·5·buses`, and
  `fairness·wait/30`. It holds through min-green, switches when a rival leads
  by more than the hysteresis, rotates at max-green, and preempts for
  ambulances — always through yellow and all-red. Every decision records a
  plain-English reason.
- **`server.py`** steps all panes in one asyncio loop and broadcasts each frame
  to every connected window so they stay in sync. It records every session to
  `recordings/`, serves the dashboard, and exposes the co-pilot endpoint.

---

## Honest caveats

- **Simulation, not the street.** SUMO emissions (HBEFA) and driver behaviour
  are models. The dollar and ton figures are illustrative, which is why the
  yearly estimate is labelled back-of-envelope.
- **CivicSignal is not universally best.** On the grid it loses throughput to
  SUMO's actuated controller (see above). It is strongest on dense, coupled
  networks and on the things weights and preemption target: emissions, transit
  and emergency response.
- **One demand per map, three seeds.** Seeds vary vehicle insertion only; the
  flow program is fixed. Different demand shapes could shift the ranking.
- **The green wave is static** (fixed offsets for one direction) and is
  expected to do badly in two-way rush traffic — it is a baseline, not a
  tuned product. On Midtown it also teleports a handful of vehicles.
- **Midtown geometry** is clipped and simplified from OSM; turn lanes and
  signal phasing are SUMO's best guess, not NYCDOT's timing plans.
- **WCAG**: the UI uses a dark theme with colour-coded strategies; full
  accessibility validation (contrast, screen-reader, keyboard-only) has not
  been done.

Map data © OpenStreetMap contributors (ODbL).
