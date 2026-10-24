# CivicSignal — 2-minute video script

All figures are from `results/compare_midtown.md` and `results/compare_grid.md`
(3 seeds, full 2400 s day). Keep the browser at `http://localhost:8000`.

Pre-roll setup (before recording): server running, map = **Midtown Manhattan**,
compare = **all four**, speed **4×**, paused at t = 0.

---

## 0:00 — Hook (0:00–0:15)

**On screen:** the four Midtown panes side by side, paused.

> "This is one city, one rush hour, four times over. Same cars, same trips —
> only the traffic lights think differently. The question every city asks:
> what if the lights could be steered?"

**Click:** play (Space). Let all four fill with traffic for ~3 s at 4×.

---

## 0:15 — Four cities (0:15–0:40)

**On screen:** all four panes running; point at each header.

> "Fixed timers. Detector-actuated. A coordinated green wave. And CivicSignal —
> a controller the city steers. Watch the queues."

**Click:** the 🔥 heat toggle (or press H). Let the heatmaps bloom for ~4 s.

> "Red is wasted fuel — cars idling at reds. By the end of the day fixed timing
> burns 1473 kilos of CO₂ here. CivicSignal: 772. That's 48% less."

---

## 0:40 — The ambulance race (0:40–1:05)

**Click:** the **Events** tab, then **🚑 Ambulance**. Watch the race bars.

> "Now send an ambulance through all four at once."

**On screen:** the race bars fill; toasts announce arrivals in order.

> "Fixed timing gets it there in about 530 seconds. CivicSignal clears a green
> corridor ahead of it and arrives in 149 — nearly four hundred seconds
> faster, a 37% cut against the best baseline."

---

## 1:05 — Chaos (1:05–1:25)

**Click:** **💥 Crash**, then **🎭 Surge** (a theatre lets out).

> "Real streets break. A crash blocks two lanes. A show lets out and dumps a
> wave of cars onto the avenue."

**On screen:** watch the fixed pane's queue balloon vs CivicSignal holding.

> "The fixed grid locks up. CivicSignal re-reads the pressure and keeps the
> intersections breathing."

---

## 1:25 — The city steers (1:25–1:45)

**Click:** the **Policy** tab.

> "And the city isn't locked out. Say the goal in plain English."

**Type in the co-pilot box:** `keep buses moving and cut emissions downtown`
→ **Ask the co-pilot** → **Apply policy**.

> "The co-pilot turns that into weights — transit up, climate up — and the
> controller shifts live. Buses here go from 281 seconds under fixed timing to
> 94. No re-timing study, no construction. Just a sentence."

**Optional:** click **Inspect** on one light to show the per-option term bars
and the plain-English reason.

---

## 1:45 — Results (1:45–2:00)

**Click:** the **Impact** tab (hero tiles + scoreboard visible).

> "Across a full Manhattan rush hour, three seeds: CivicSignal wins ten of
> twelve metrics. Delay down 85% against fixed. Emissions down 48%.
> Ambulances a third faster. Same roads, same cars — smarter lights."

**Hold** on the scoreboard, stars on CivicSignal.

> "Traffic lights the city can steer. That's CivicSignal."

---

## Judge Q&A — prepared answers

**Is this real or faked?** Real microsimulation in Eclipse SUMO 1.28. Four
independent simulations, identical demand, driven live over TraCI. Every number
on screen and in the README comes from `results/`, reproducible with
`python compare.py --scenario all --seeds 1 2 3`.

**Does CivicSignal always win?** No — and we show it. On the synthetic grid,
SUMO's actuated controller beats us on raw delay (6.1 s vs 10.5 s) and CO₂,
because isolated detector intersections are exactly its sweet spot. CivicSignal
wins where it's designed to: dense coupled networks like Midtown, plus transit
and emergency response on every map (grid ambulance 76.4 s vs 105.7 s best
baseline).

**How does the controller decide?** Weighted max-pressure. Each possible green
scores four terms — throughput (in-queue minus out-queue), a climate term that
protects moving platoons from a costly restart, a transit term for buses, and a
fairness term for the longest wait. Highest score wins, subject to min/max green
and hysteresis so it doesn't flicker. The Inspect tab shows the live math.

**Why does the climate weight help emissions?** A car restarting from a dead
stop burns far more than one idling or rolling. The climate term rewards keeping
approaching platoons moving, so fewer vehicles have to stop and re-accelerate —
that's where the idle-CO₂ and fuel savings come from.

**What about the green corridor — does it starve cross traffic?** Preemption
only triggers within 300 m of a light on the ambulance's path, holds that one
green, and always inserts yellow and all-red before any change. It's a brief,
localized hold, not a network-wide override.

**What's the co-pilot doing?** It maps a plain-English goal to the four weights
via Gemini with a strict JSON schema (each weight 0–3). It's a convenience layer
over the same sliders — the controller is fully deterministic without it.

**Biggest limitation?** It's a simulation with modelled emissions and one demand
profile per map. The yearly dollar and ton figures are back-of-envelope. Next
step is validating against real signal-timing and count data.
