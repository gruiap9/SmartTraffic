# .kiro — Kiro configuration

This directory holds the Kiro features used to build CivicSignal.

## steering/
Project context Kiro loads into every (or matched) request.
- `project-rules.md` — always-on hard constraints (Python 3.12, headless SUMO
  1.28.0, `from __future__ import annotations`, no frontend build step, the
  fixed frame contract, the verification rule).
- `architecture.md` — loaded when editing `core/*.py`; the exact dataclasses,
  the controller's scoring/timing, and the runner's responsibilities.

## specs/civicsignal/
The 11-step build plan as a Kiro spec.
- `requirements.md` — user stories and acceptance criteria.
- `design.md` — architecture, components, data flow.
- `tasks.md` — the implementation tasks (all complete), each linked to
  requirements.
