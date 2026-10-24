# How we used Kiro to build CivicSignal

CivicSignal — a live dashboard that runs four SUMO traffic-signal strategies
side by side and lets a city steer an adaptive controller — was built almost
entirely through Kiro. This write-up is an honest account of which Kiro
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

The 11 prompts were a working spec before we formalized them. We later captured
that structure as a proper Kiro spec under `.kiro/specs/civicsignal/`:

- **`requirements.md`** — user stories with EARS-style acceptance criteria
  (e.g. "THE grid scenario SHALL have exactly 9 controllable lights; THE
  midtown scenario SHALL have exactly 54").
- **`design.md`** — architecture, components, and the fixed frame data
  contract.
- **`tasks.md`** — the 11 implementation tasks, each linked back to the
  requirements it satisfies.

**How the spec approach improved development, vs vibe coding.** Each prompt had
an explicit, checkable success criterion ("9 controllable lights", "exactly 54
signals", "all controller tests pass"), so the agent could not drift. Because
step N+1 depended on step N (the runner needs the scenario; the benchmark needs
the controller), gating each step on its check kept a large build coherent.
Pure vibe coding would have produced plausible-looking code that failed only at
integration — instead, every layer was verified against a concrete target
before the next was built. The acceptance criteria doubled as a test plan.

---

## Steering docs

Steering made the biggest single difference to response quality. We keep two
docs in `.kiro/steering/`:

- **`project-rules.md`** (`inclusion: always`) — the hard constraints that must
  never be violated: Python 3.12, headless SUMO 1.28.0 from pip, **every module
  starts with `from __future__ import annotations`**, no frontend build step,
  the exact frame JSON contract, and "a zero exit code is not proof of
  success." These rules were injected into every request, so the agent applied
  them without being re-told.
- **`architecture.md`** (`inclusion: fileMatch`, `core/*.py`) — the exact
  dataclasses, the controller's four-term scoring and timing constants, and the
  runner's responsibilities. Scoping it to `core/` keeps that detail out of
  unrelated edits while guaranteeing it is present when it matters.

**The strategy that mattered most:** encoding the invariants (the frame
contract, the dataclass shapes, the headless-only rule) as *always-on* steering
rather than repeating them per prompt. It removed a whole class of drift — the
agent never once proposed `sumo-gui`, an npm build, or a module missing the
`__future__` import.

---

## Manual tuning after the agent build

Spec-driven generation got us a complete, verified system; we then went in by
hand to adjust:

- **Dashboard sizing** — tuned the stage split so the two map panes dominate
  (~79%) over the chart strip (~21%, a 3.8:1 flex ratio) and set the sidebar
  width to match a reference screenshot. We added a `?v=N` cache-bust on the
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

## Features we did not use (in the interest of honesty)

- **Agent hooks** — we prototyped hooks (test-on-save, a constraint check, a
  verify-output reminder) and they did fire live during the session, but we
  removed the `hooks/` folder from the final submission to keep `.kiro/`
  focused on steering and the spec.
- **MCP** — not used. The build needed only SUMO (via pip) and the Overpass
  HTTP API, both reachable directly; no external tool server was required. An
  MCP server wrapping a live traffic-data or mapping provider would be the
  natural next extension.
- **Property-based testing** — not used. Our controller tests are
  example-based (`tests/test_controller.py`, 13 cases on a fake 4-link light).
  PBT would fit well for invariants like "the controller never skips yellow
  between two greens" or "score is monotonic in queue length" — a clear next
  step.
- **Kiro powers** — none installed; no third-party power-bundled tooling was
  used.
- **Kiro CLI / Web / Crew** — not used; a single IDE Autopilot session covered
  the whole build.

---

## Bottom line

Kiro's IDE agent, driven by a spec-shaped sequence of 11 prompts and governed
by always-on steering rules, built a working multi-simulation traffic system
end to end — and its run-inspect-fix loop caught integration bugs (dropped bus
flows, joined-TLS id crashes, the Overpass 406) that code review alone would
have missed. We then tuned sizing, data, and the grid by hand. The honest
result: Kiro did the heavy lifting on breadth and correctness; we supplied the
last-mile judgment.
