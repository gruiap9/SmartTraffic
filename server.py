from __future__ import annotations

"""CivicSignal server.

One FastAPI process serves the dashboard (static web/) and a websocket on
localhost:8000. A single asyncio loop steps all panes and broadcasts each frame
to every connected window, so all windows stay in sync.

CLI also supports headless, deterministic replays/recordings.
"""

import argparse
import asyncio
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from core.controller import WeightedMaxPressure, BALANCED
from core.sumo_runner import SumoRunner
from core.types import Weights

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
SCEN_DIR = os.path.join(ROOT, "scenario")
REC_DIR = os.path.join(ROOT, "recordings")

STRATEGIES = ["fixed", "actuated", "greenwave", "adaptive"]
STRATEGY_LABEL = {
    "fixed": "fixed",
    "actuated": "actuated",
    "greenwave": "green wave",
    "adaptive": "CivicSignal",
}

# Default pane sets.
PANES_2 = ["fixed", "adaptive"]
PANES_4 = ["fixed", "actuated", "greenwave", "adaptive"]


def list_scenarios() -> list[dict]:
    out = []
    if not os.path.isdir(SCEN_DIR):
        return out
    for sid in sorted(os.listdir(SCEN_DIR)):
        meta_path = os.path.join(SCEN_DIR, sid, "meta.json")
        if os.path.exists(meta_path):
            meta = json.load(open(meta_path))
            out.append({"id": sid, "title": meta.get("title", sid)})
    return out


def _scenario_meta(scenario: str) -> dict:
    return json.load(open(os.path.join(SCEN_DIR, scenario, "meta.json")))


def _scenario_cfg(scenario: str) -> str:
    return os.path.join(SCEN_DIR, scenario, "sim.sumocfg")


# ----------------------------------------------------------------------
# Sim: holds panes + shared state.
# ----------------------------------------------------------------------

class Sim:
    def __init__(self, scenario: str = "grid", panes: Optional[list[str]] = None,
                 record: bool = True, seed: int = 1):
        self.scenario = scenario
        self.panes = panes or list(PANES_2)
        self.weights = Weights(**BALANCED)
        self.playing = True
        self.sps = 10            # steps per second (1x)
        self.seed = seed
        self.pending_events: list[dict] = []
        self.inspected: Optional[str] = None
        self.followed: Optional[str] = None
        self.history: list = []   # [t, {pane: [idle_co2, queue]}]
        self.t = 0

        self._runners: dict[str, SumoRunner] = {}
        self._controllers: dict[str, WeightedMaxPressure] = {}
        self._last_frame: Optional[dict] = None
        self._lock = threading.Lock()
        self._record = record
        self._rec_file = None
        self._copilot_available = _copilot_available()

        self._build()

    # -- lifecycle --

    def _build(self) -> None:
        meta = _scenario_meta(self.scenario)
        cfg = _scenario_cfg(self.scenario)
        self._end = meta.get("end", 2400)
        self._meta = meta
        self._runners.clear()
        self._controllers.clear()
        self.t = 0
        self.history = []

        for pane in self.panes:
            ctrl = None
            program = "0"
            if pane == "adaptive":
                ctrl = WeightedMaxPressure(self.weights)
                self._controllers[pane] = ctrl
            elif pane in ("actuated", "greenwave"):
                program = pane
            label = f"{self.scenario}_{pane}_{self.seed}_{int(time.time()*1000)}"
            self._runners[pane] = SumoRunner(
                label, cfg, seed=self.seed, controller=ctrl, program=program)

        if self._record:
            self._open_recording()

    def _open_recording(self) -> None:
        os.makedirs(REC_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(REC_DIR, f"{ts}-{self.scenario}.jsonl")
        self._rec_file = open(path, "w")
        # First line: state message (so a replay can bootstrap).
        self._rec_file.write(json.dumps(self.state_message()) + "\n")

    def close(self) -> None:
        for r in self._runners.values():
            r.close()
        if self._rec_file:
            self._rec_file.close()
            self._rec_file = None

    # -- stepping --

    def step_once(self) -> dict:
        """Step every pane once and return the broadcast frame."""
        with self._lock:
            # Drain pending events into every pane (deterministic ordering).
            events_fired = self._apply_pending_events()

            sims = {}
            for pane, runner in self._runners.items():
                fsim = runner.step()
                sims[pane] = fsim
            self.t = next(iter(self._runners.values())).t if self._runners else self.t

            # Per-pane inspect / follow enrichment.
            inspect = {}
            follow = {}
            for pane, runner in self._runners.items():
                if self.inspected:
                    inspect[pane] = runner.inspect(self.inspected)
                if self.followed:
                    follow[pane] = runner.trip(self.followed)

            frame = {
                "type": "frame",
                "t": self.t,
                "scenario": self.scenario,
                "sims": sims,
                "events": events_fired,
            }
            if inspect:
                frame["inspect"] = inspect
            if follow:
                frame["follow"] = follow

            # History: idle CO2 + queue per pane.
            snap = {pane: [sims[pane]["m"]["idle_co2_kg"],
                           sims[pane]["m"]["queue"]] for pane in sims}
            self.history.append([self.t, snap])

            self._last_frame = frame
            if self._rec_file:
                self._rec_file.write(json.dumps(frame) + "\n")
            return frame

    def _apply_pending_events(self) -> list[dict]:
        fired = []
        if not self.pending_events:
            return fired
        events = self.pending_events
        self.pending_events = []
        for ev in events:
            kind = ev.get("kind")
            for k, (pane, runner) in enumerate(self._runners.items()):
                self._fire_event(runner, kind, k)
            fired.append({"kind": kind, "t": self.t,
                          "label": self._event_label(kind)})
        return fired

    def _fire_event(self, runner: SumoRunner, kind: str, k: int) -> None:
        meta = self._meta
        if kind == "ambulance":
            route = meta.get("ambulance", {}).get("route", "amb_route")
            runner.spawn_ambulance(route)
        elif kind == "surge":
            s = meta.get("surge", {})
            runner.surge(s.get("from", []), s.get("to", []),
                         s.get("n", 100), s.get("over", 240), k=k)
        elif kind == "crash":
            c = meta.get("crash", {})
            runner.crash(c.get("edge", ""), c.get("lanes", [0]),
                         c.get("pos", 20.0), c.get("duration", 300))

    def _event_label(self, kind: str) -> str:
        meta = self._meta
        if kind == "ambulance":
            return meta.get("ambulance", {}).get("label", "Ambulance")
        if kind == "surge":
            return meta.get("surge", {}).get("label", "Surge")
        if kind == "crash":
            return meta.get("crash", {}).get("label", "Crash")
        return kind

    # -- control actions --

    def set_weights(self, w: dict) -> None:
        with self._lock:
            self.weights = Weights(
                flow=float(w.get("flow", self.weights.flow)),
                climate=float(w.get("climate", self.weights.climate)),
                transit=float(w.get("transit", self.weights.transit)),
                fairness=float(w.get("fairness", self.weights.fairness)),
                preempt=bool(w.get("preempt", self.weights.preempt)),
            )
            for ctrl in self._controllers.values():
                ctrl.set_weights(self.weights)

    def queue_event(self, kind: str) -> None:
        with self._lock:
            self.pending_events.append({"kind": kind})

    def set_speed(self, sps: float) -> None:
        self.sps = max(1, min(100, int(sps)))

    def set_inspect(self, tls: Optional[str]) -> None:
        self.inspected = tls

    def set_follow(self, vid: Optional[str]) -> None:
        self.followed = vid

    def reconfigure(self, scenario: Optional[str], panes: Optional[list[str]]):
        """Rebuild sims on a new scenario / pane set (run in worker thread)."""
        with self._lock:
            self.close()
            if scenario:
                self.scenario = scenario
            if panes:
                self.panes = panes
            self._build()

    # -- messages --

    def state_message(self) -> dict:
        return {
            "type": "state",
            "weights": self._weights_dict(),
            "playing": self.playing,
            "sps": self.sps,
            "t": self.t,
            "scenario": self.scenario,
            "panes": self.panes,
            "scenarios": list_scenarios(),
            "labels": STRATEGY_LABEL,
            "copilot": self._copilot_available,
            "inspected": self.inspected,
            "followed": self.followed,
            "end": self._end,
            "meta": {
                "title": self._meta.get("title"),
                "rush": self._meta.get("rush"),
                "rotate_deg": self._meta.get("rotate_deg", 0),
                "ambulance": self._meta.get("ambulance", {}).get("label"),
                "surge": self._meta.get("surge", {}).get("label"),
                "crash": self._meta.get("crash", {}).get("label"),
                "attribution": self._meta.get("attribution", ""),
            },
        }

    def _weights_dict(self) -> dict:
        w = self.weights
        return {"flow": w.flow, "climate": w.climate, "transit": w.transit,
                "fairness": w.fairness, "preempt": w.preempt}


# ----------------------------------------------------------------------
# Copilot (Gemini).
# ----------------------------------------------------------------------

def _read_env(key: str) -> Optional[str]:
    """Read a key from .env, tolerating spaces around '='."""
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        return os.environ.get(key)
    for line in open(env_path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return os.environ.get(key)


def _copilot_available() -> bool:
    return bool(_read_env("GEMINI_API_KEY"))


COPILOT_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]

COPILOT_SCHEMA = {
    "type": "object",
    "properties": {
        "flow": {"type": "number"},
        "climate": {"type": "number"},
        "transit": {"type": "number"},
        "fairness": {"type": "number"},
        "preempt": {"type": "boolean"},
        "event": {"type": "string"},
        "title": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["flow", "climate", "transit", "fairness", "preempt",
                 "title", "explanation"],
}


def copilot_ask(goal: str) -> dict:
    key = _read_env("GEMINI_API_KEY")
    if not key:
        return {"error": "GEMINI_API_KEY not set"}

    prompt = (
        "You steer a city's adaptive traffic controller. Translate the goal "
        "into four weights in 0..3 (flow, climate, transit, fairness), a "
        "preempt boolean, and optionally one event from "
        "[ambulance, surge, crash] or 'none'. Give a short title and a "
        "one-sentence explanation.\nGoal: " + goal
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": COPILOT_SCHEMA,
        },
    }
    data = json.dumps(body).encode()

    last_err = None
    for model in COPILOT_MODELS:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            parsed["model"] = model
            parsed = _clamp_copilot(parsed)
            return parsed
        except urllib.error.HTTPError as e:
            last_err = f"{e.code}"
            if e.code in (400, 404, 429) or 500 <= e.code < 600:
                continue  # fall through to next model
            break
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
    return {"error": f"all models failed ({last_err})"}


def _clamp_copilot(p: dict) -> dict:
    for k in ("flow", "climate", "transit", "fairness"):
        try:
            p[k] = max(0.0, min(3.0, float(p.get(k, 0))))
        except (TypeError, ValueError):
            p[k] = 0.0
    p["preempt"] = bool(p.get("preempt", True))
    ev = p.get("event", "none")
    if ev not in ("ambulance", "surge", "crash", "none"):
        ev = "none"
    p["event"] = ev
    return p


# ----------------------------------------------------------------------
# App + websocket loop.
# ----------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop_task
    get_sim()
    _loop_task = asyncio.create_task(_sim_loop())
    try:
        yield
    finally:
        if _loop_task:
            _loop_task.cancel()
        if _sim:
            _sim.close()


app = FastAPI(lifespan=_lifespan)

_sim: Optional[Sim] = None
_clients: set[WebSocket] = set()
_clients_lock = asyncio.Lock()
_loop_task: Optional[asyncio.Task] = None
_restart_lock = threading.Lock()


def get_sim() -> Sim:
    global _sim
    if _sim is None:
        _sim = Sim()
    return _sim


async def _broadcast(msg: dict) -> None:
    dead = []
    async with _clients_lock:
        targets = list(_clients)
    text = json.dumps(msg)
    for ws in targets:
        try:
            await ws.send_text(text)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    if dead:
        async with _clients_lock:
            for ws in dead:
                _clients.discard(ws)


async def _sim_loop() -> None:
    sim = get_sim()
    while True:
        if sim.playing and sim.t < sim._end:
            # Step in a thread so the event loop isn't blocked by SUMO.
            frame = await asyncio.to_thread(sim.step_once)
            await _broadcast(frame)
            await asyncio.sleep(1.0 / max(1, sim.sps))
        else:
            await asyncio.sleep(0.1)


@app.get("/")
async def index(view: Optional[str] = None) -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.post("/api/copilot")
async def api_copilot(payload: dict) -> JSONResponse:
    goal = payload.get("goal", "")
    result = await asyncio.to_thread(copilot_ask, goal)
    return JSONResponse(result)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    sim = get_sim()
    async with _clients_lock:
        _clients.add(ws)
    # On connect: state, then history, then last frame.
    await ws.send_text(json.dumps(sim.state_message()))
    await ws.send_text(json.dumps({"type": "history", "data": sim.history}))
    if sim._last_frame:
        await ws.send_text(json.dumps(sim._last_frame))

    try:
        while True:
            raw = await ws.receive_text()
            await _handle_message(sim, json.loads(raw))
    except WebSocketDisconnect:
        pass
    finally:
        async with _clients_lock:
            _clients.discard(ws)


async def _handle_message(sim: Sim, msg: dict) -> None:
    mtype = msg.get("type")
    if mtype == "weights":
        sim.set_weights(msg.get("weights", msg))
        await _broadcast(sim.state_message())
    elif mtype == "event":
        sim.queue_event(msg.get("event") or msg.get("kind"))
    elif mtype == "control":
        action = msg.get("action")
        if action == "play":
            sim.playing = True
        elif action == "pause":
            sim.playing = False
        elif action == "reset":
            await asyncio.to_thread(_restart, sim, None, None)
        await _broadcast(sim.state_message())
    elif mtype == "speed":
        sim.set_speed(msg.get("sps", sim.sps))
        await _broadcast(sim.state_message())
    elif mtype == "config":
        scenario = msg.get("scenario")
        panes = msg.get("panes")
        await asyncio.to_thread(_restart, sim, scenario, panes)
        await _broadcast(sim.state_message())
        if sim._last_frame:
            await _broadcast(sim._last_frame)
    elif mtype == "inspect":
        sim.set_inspect(msg.get("tls"))
    elif mtype == "follow":
        sim.set_follow(msg.get("vid"))


def _restart(sim: Sim, scenario, panes) -> None:
    """Guarded restart run in a worker thread."""
    with _restart_lock:
        sim.reconfigure(scenario, panes)


# Static files (vendor, app.js, style.css, scenarios).
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
app.mount("/recordings", StaticFiles(directory=REC_DIR), name="recordings")


# ----------------------------------------------------------------------
# Headless replay / recording (CLI).
# ----------------------------------------------------------------------

def record_headless(path: str, scenario: str, panes: list[str], end: int,
                    events: list[tuple[int, str]]) -> None:
    """Deterministic headless run that writes a .jsonl recording.

    The first line is the state message.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sim = Sim(scenario=scenario, panes=panes, record=False, seed=1)
    sim._end = end
    ev_by_t: dict[int, list[str]] = {}
    for (t, kind) in events:
        ev_by_t.setdefault(t, []).append(kind)

    with open(path, "w") as f:
        f.write(json.dumps(sim.state_message()) + "\n")
        while sim.t < end:
            for kind in ev_by_t.get(sim.t, []):
                sim.queue_event(kind)
            frame = sim.step_once()
            f.write(json.dumps(frame) + "\n")
    sim.close()
    print(f"[record] wrote {path} ({sim.t}s)")


def _parse_events(spec: str) -> list[tuple[int, str]]:
    out = []
    if not spec:
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        t, _, kind = part.partition(":")
        out.append((int(t), kind.strip()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--record", help="output .jsonl path for a headless replay")
    ap.add_argument("--scenario", default="grid")
    ap.add_argument("--panes", default="fixed,adaptive")
    ap.add_argument("--end", type=int, default=1500)
    ap.add_argument("--events", default="",
                    help="e.g. 950:ambulance,1000:surge")
    args = ap.parse_args(argv)

    if args.record:
        panes = [p.strip() for p in args.panes.split(",") if p.strip()]
        events = _parse_events(args.events)
        record_headless(args.record, args.scenario, panes, args.end, events)
        return 0

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
