# How we used Kiro to build CivicSignal

CivicSignal — a live dashboard that runs four SUMO traffic-signal strategies
side by side and lets a city steer an adaptive controller — was built almost
entirely through Kiro. This document is an honest account of which Kiro
features we used, how, and where they made the difference. We call out the
features we did **not** use so judges can weigh the project accurately.

---

## The workflow in one sentence

We drove Kiro's **IDE agent** through **11 structured prompts** that map 1:1 to
a spec (brief → setup → grid → Midtown → runner → controller → benchmark →
server → dashboard → sidebar → verify), and then **manually tuned** the result
— dashboard sizing, data inputs, and grid adjustments.

---

## Which Kiro tools we used

**Kiro IDE agent (Autopilot)** — the entire build ran here. We did **not** use
the Kiro **CLI**, **Web**, or **Crew**; a single long IDE session was the right
fit for a staged, dependency-heavy build where each step had to be verified
before the next.

What the IDE agent gave us that plain chat would not:

- **Tool-driven edits across ~40 files.** The agent authored and then
  surgically patched `core/controller.py`, `core/sumo_runner.py`, `server.py`,
  and the vanilla-JS frontend with targeted edits instead of rewriting files.
- **Run → inspect → fix loops that caught real bugs.** Several defects were
  only visible by running things:
  - SUMO silently **dropped the bus flow** because route files must be sorted
    by departure time — found by stepping the sim and checking `getIDList()`,
    fixed with a `_sort_routes_by_begin` helper.
  - `--tls.join` on Midtown produced **cluster TLS ids that are not node ids**,
    crashing `getNode(tid)` — fixed with a `tls_coord()` fallback averaging the
    controlled stop-lines.
  - The Overpass API returned **406** for a descriptive User-Agent; the agent
    probed with `curl` until a clean token-style UA returned 200, then wired
    that into the downloader.
- **Background process management** to run the FastAPI server and the headless
  recorder as long-lived processes while continuing to work — that is how
  `recordings/demo.jsonl` (1500 s with timed events) and the live
  `localhost:8000` server were produced without blocking the session.

---

## Spec-driven development

The 11 prompts were a working spec: each described one build step with an
explicit, checkable success criterion.

1. Project brief and constraints
2. Setup and smoke test (venv, SUMO 1.28.0, Chart.js)
3. Grid scenario — check: 9 controllable lights
4. Midtown Manhattan from OpenStreetMap — check: exactly 54 signals
5. Simulation runner (multi-sim over TraCI)
6. Controller and tests — check: all controller tests pass
7. Benchmark (parallel, writes results/)
8. FastAPI server (websocket, co-pilot, replay)
9. Dashboard, maps and layout
10. Dashboard, sidebar and features
11. Verify, record and document

**How the spec approach improved development, vs vibe coding.** Because each
prompt had a concrete target ("9 controllable lights", "exactly 54 signals",
"all controller tests pass") the agent could not drift, and because step N+1
depended on step N (the runner needs the scenario; the benchmark needs the
controller), gating each step on its check kept a large build coherent. Pure
vibe coding would have produced plausible-looking code that failed only at
integration — instead, every layer was verified against a concrete target
before the next was built. The acceptance criteria doubled as a test plan.

---

## Steering (invariants the agent never broke)

The single highest-leverage technique was giving Kiro the project's hard
invariants as standing rules so they applied to every response without being
repeated:

- Python 3.12; SUMO 1.28.0 from pip; **headless only** (never `sumo-gui`).
- **Every Python module starts with `from __future__ import annotations`.**
- Frontend is plain HTML/CSS/JS with vendored Chart.js 4.4.1; **no build step**.
- The fixed frame JSON contract and the exact `Weights` / `TLSInfo` / `LaneObs`
  dataclass shapes.
- "A zero exit code is not proof of success" — check real output.

The payoff: across a ~40-file build the agent never once proposed `sumo-gui`,
an npm build, or a module missing the `__future__` import, and never broke the
frame contract the server and frontend both depend on.

---

## Manual tuning after the agent build

Spec-driven generation produced a complete, verified system; we then went in by
hand to adjust:

- **Dashboard sizing** — tuned the stage split so the two map panes dominate
  (~79%) over the chart strip (~21%, a 3.8:1 flex ratio) and set the sidebar
  width to match a reference screenshot. Added a `?v=N` cache-bust on the
  stylesheet link so CSS edits show on a normal browser refresh.
- **Data inputs** — adjusted demand profiles, event specs (surge size, crash
  lanes), and the Midtown bounding box / corner intersections used to clip the
  network.
- **Grid adjustments** — refined the grid scenario's flows, bus route, and
  ambulance endpoints, and reconciled the honest finding that SUMO's actuated
  controller beats CivicSignal on the toy grid (documented plainly in the
  README) while CivicSignal dominates on congested Midtown.

This split — agent for breadth and correctness, humans for taste and
fine-tuning — is where Kiro was most effective.

---

## Agent hooks

Three hooks live in `.kiro/hooks/`, automating the guardrails we would
otherwise apply by hand:

- **`test-on-core-save`** (`PostFileSave`, matches `core/*.py`) — runs
  `pytest` on every save of a core module, so a controller or runner
  regression surfaces immediately.
- **`check-future-annotations`** (`PostFileSave`, matches `*.py`) — warns when
  a saved Python file is missing the required
  `from __future__ import annotations` header.
- **`verify-sim-output`** (`PreToolUse`, matches `execute_bash`) — before a
  shell command runs, reminds the agent that a zero exit code is not proof of
  success for a simulation or benchmark. This one fired live during the build
  and genuinely changed how results were verified.

Together they turn two project rules (tests pass; verify real output) into
automation instead of discipline.

## Features we did not use (in the interest of honesty)

- **MCP** — not used. The build needed only SUMO (via pip) and the Overpass
  HTTP API, both reachable directly; no external tool server was required. An
  MCP server wrapping a live traffic-data or mapping provider would be the
  natural next extension.
- **Property-based testing** — not used. Our controller tests are
  example-based (`tests/test_controller.py`, 13 cases on a fake 4-link light).
  PBT would fit invariants like "the controller never skips yellow between two
  greens" or "score is monotonic in queue length" — a clear next step.
- **Kiro powers** — none installed; no third-party power-bundled tooling.
- **Kiro CLI / Web / Crew** — not used; one IDE Autopilot session covered the
  whole build.

---

## Bottom line

Kiro's IDE agent, driven by a spec-shaped sequence of 11 prompts and governed
by always-on invariants, built a working multi-simulation traffic system end to
end — and its run-inspect-fix loop caught integration bugs (dropped bus flows,
joined-TLS id crashes, the Overpass 406) that code review alone would have
missed. We then tuned sizing, data, and the grid by hand. The honest result:
Kiro did the heavy lifting on breadth and correctness; we supplied the
last-mile judgment.
