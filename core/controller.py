from __future__ import annotations

"""CivicSignal controller: a weighted max-pressure traffic-signal policy.

The city steers it with four weights (flow, climate, transit, fairness) plus a
preemption toggle. Each controlled light is scored per candidate green state;
the controller holds, switches, or rotates subject to min/max green and
hysteresis, inserts yellow + all-red between greens, and gives ambulances a
green corridor.
"""

from dataclasses import dataclass, field
from typing import Optional

from core.types import LaneObs, TLSInfo, Weights

# Timing constants (seconds).
MIN_GREEN = 8
MAX_GREEN = 60
YELLOW = 3
ALL_RED = 1
HYST = 2.0
PREEMPT_DIST_M = 300.0

# Default city policy.
BALANCED = dict(flow=1, climate=1, transit=0.5, fairness=0, preempt=True)


@dataclass
class _Option:
    """A scored candidate green state for one light."""
    state: str
    terms: dict[str, float]
    total: float
    served_in: list[str]
    served_pairs: list[tuple[str, str]]
    is_current: bool = False
    is_target: bool = False
    streets: list[str] = field(default_factory=list)


@dataclass
class _Phase:
    """The live phase of one controlled light."""
    state: str                      # current displayed state
    green: str                      # the green we are serving (or transitioning to)
    since: int = 0                  # time the current green started
    mode: str = "green"             # green | yellow | all_red
    target: Optional[str] = None    # green we are transitioning to
    phase_until: int = 0            # when yellow/all_red ends
    reason: str = ""
    last_options: list[_Option] = field(default_factory=list)
    last_terms: dict[str, float] = field(default_factory=dict)
    last_total: float = 0.0
    preempt: bool = False           # currently holding for an ambulance


class WeightedMaxPressure:
    def __init__(self, weights: Weights | dict):
        self.weights = self._coerce(weights)
        self.infos: dict[str, TLSInfo] = {}
        self.phases: dict[str, _Phase] = {}
        # Cache of (in_lane -> served pairs) per green per light.
        self._served: dict[str, dict[str, list[tuple[str, str]]]] = {}

    # ------------------------------------------------------------------
    # Setup / weights
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce(w: Weights | dict) -> Weights:
        if isinstance(w, Weights):
            return w
        return Weights(
            flow=float(w.get("flow", 1.0)),
            climate=float(w.get("climate", 0.0)),
            transit=float(w.get("transit", 0.0)),
            fairness=float(w.get("fairness", 0.0)),
            preempt=bool(w.get("preempt", True)),
        )

    def set_weights(self, weights: Weights | dict) -> None:
        """Weights change live."""
        self.weights = self._coerce(weights)

    def register(self, info: TLSInfo) -> None:
        self.infos[info.tls_id] = info
        # Start on the first green state.
        g0 = info.green_states[0] if info.green_states else info.initial_state
        self.phases[info.tls_id] = _Phase(state=g0, green=g0, since=0,
                                          mode="green")
        # Precompute served (in,out) pairs per green state.
        served: dict[str, list[tuple[str, str]]] = {}
        for g in info.green_states:
            pairs = []
            for i, (in_lane, out_lane) in enumerate(info.links):
                if i < len(g) and g[i] in "Gg" and in_lane:
                    pairs.append((in_lane, out_lane))
            served[g] = pairs
        self._served[info.tls_id] = served

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_state(self, tid: str, g: str,
                     lanes: dict[str, LaneObs]) -> _Option:
        w = self.weights
        pairs = self._served[tid][g]
        seen_pairs: set[tuple[str, str]] = set()
        served_in_lanes: list[str] = []
        seen_in: set[str] = set()

        flow_term = 0.0
        climate_term = 0.0
        transit_term = 0.0
        fairness_term = 0.0

        for (in_lane, out_lane) in pairs:
            # Flow: per unique served (in,out) pair.
            key = (in_lane, out_lane)
            if key not in seen_pairs:
                seen_pairs.add(key)
                in_h = lanes.get(in_lane, LaneObs()).halting
                out_h = lanes.get(out_lane, LaneObs()).halting if out_lane else 0
                flow_term += w.flow * (in_h - out_h)
            # Per served in-lane terms (unique in-lanes).
            if in_lane not in seen_in:
                seen_in.add(in_lane)
                served_in_lanes.append(in_lane)
                o = lanes.get(in_lane, LaneObs())
                climate_term += w.climate * o.approaching
                transit_term += w.transit * 5.0 * (o.buses + o.buses_moving)
                fairness_term += w.fairness * (o.max_wait_s / 30.0)

        terms = {
            "flow": round(flow_term, 3),
            "climate": round(climate_term, 3),
            "transit": round(transit_term, 3),
            "fairness": round(fairness_term, 3),
        }
        total = flow_term + climate_term + transit_term + fairness_term
        return _Option(
            state=g, terms=terms, total=round(total, 3),
            served_in=served_in_lanes,
            served_pairs=list(seen_pairs),
            streets=self._street_names(tid, served_in_lanes),
        )

    def _score_all(self, tid: str, lanes: dict[str, LaneObs]) -> list[_Option]:
        info = self.infos[tid]
        opts = [self._score_state(tid, g, lanes) for g in info.green_states]
        return opts

    # ------------------------------------------------------------------
    # Step
    # ------------------------------------------------------------------

    def step(self, tls_id: str, t: int, lanes: dict[str, LaneObs]) -> str:
        info = self.infos.get(tls_id)
        if info is None:
            return ""
        ph = self.phases[tls_id]

        # --- handle an in-progress transition (yellow / all-red) ---
        if ph.mode == "yellow":
            if t >= ph.phase_until:
                ph.mode = "all_red"
                ph.state = self._all_red(info, ph.green)
                ph.phase_until = t + ALL_RED
                ph.reason = "all-red clearance"
            return ph.state
        if ph.mode == "all_red":
            if t >= ph.phase_until:
                ph.mode = "green"
                ph.green = ph.target or ph.green
                ph.state = ph.green
                ph.since = t
                ph.target = None
                ph.reason = "green started"
            return ph.state

        # --- score options for a live green ---
        opts = self._score_all(tls_id, lanes)
        by_state = {o.state: o for o in opts}
        cur_opt = by_state.get(ph.green)
        if cur_opt:
            cur_opt.is_current = True
        best_opt = max(opts, key=lambda o: o.total)

        ph.last_options = opts
        ph.last_terms = cur_opt.terms if cur_opt else {}
        ph.last_total = cur_opt.total if cur_opt else 0.0

        elapsed = t - ph.since

        # --- preemption: ambulance within range targets the serving green ---
        preempt_target = None
        if self.weights.preempt:
            preempt_target = self._preempt_target(tls_id, lanes)
        if preempt_target is not None:
            ph.preempt = True
            if preempt_target == ph.green:
                # Hold this green while the ambulance approaches (ignores
                # min-green and max-green).
                ph.reason = "holding green for ambulance"
                return ph.state
            else:
                # Switch toward the ambulance's green. Ignore min-green, but
                # never skip yellow.
                ph.reason = "preempting: switching green for ambulance"
                return self._begin_transition(info, ph, preempt_target, t)
        else:
            ph.preempt = False

        # --- normal switching logic ---
        if elapsed < MIN_GREEN:
            ph.reason = f"minimum green ({MIN_GREEN} s)"
            return ph.state

        if elapsed >= MAX_GREEN:
            # Rotate to the next best state (not the current).
            others = [o for o in opts if o.state != ph.green]
            if others:
                nxt = max(others, key=lambda o: o.total)
                ph.reason = f"maximum green ({MAX_GREEN} s): rotating"
                nxt.is_target = True
                return self._begin_transition(info, ph, nxt.state, t)
            ph.reason = f"maximum green ({MAX_GREEN} s): no alternative"
            return ph.state

        # Switch only if best beats current by more than HYST.
        if cur_opt is not None and best_opt.state != ph.green:
            if best_opt.total > cur_opt.total + HYST:
                ph.reason = "another direction scores higher: switching"
                best_opt.is_target = True
                return self._begin_transition(info, ph, best_opt.state, t)
            else:
                ph.reason = "holding: lead within hysteresis"
                return ph.state

        ph.reason = "holding: current direction still best"
        return ph.state

    # ------------------------------------------------------------------
    # Transitions / preemption
    # ------------------------------------------------------------------

    def _begin_transition(self, info: TLSInfo, ph: _Phase,
                          target_green: str, t: int) -> str:
        yellow = self._yellow(info, ph.green, target_green)
        ph.mode = "yellow"
        ph.target = target_green
        ph.state = yellow
        ph.phase_until = t + YELLOW
        return ph.state

    @staticmethod
    def _yellow(info: TLSInfo, cur: str, target: str) -> str:
        """Yellow where current is green and the target isn't."""
        out = []
        for i in range(len(cur)):
            c = cur[i]
            tgt = target[i] if i < len(target) else "r"
            if c in "Gg" and tgt not in "Gg":
                out.append("y")
            elif c in "Gg":
                out.append(c)   # stays green through the change
            else:
                out.append("r")
        return "".join(out)

    @staticmethod
    def _all_red(info: TLSInfo, cur: str) -> str:
        return "r" * len(cur)

    def _preempt_target(self, tid: str,
                        lanes: dict[str, LaneObs]) -> Optional[str]:
        """If an ambulance is within PREEMPT_DIST_M on some served in-lane,
        return the green state that serves that lane (closest ambulance wins)."""
        info = self.infos[tid]
        best_lane = None
        best_dist = PREEMPT_DIST_M
        for in_lane, _out in info.links:
            o = lanes.get(in_lane)
            if o is None or o.emergency_dist_m is None:
                continue
            if o.emergency_dist_m <= best_dist:
                best_dist = o.emergency_dist_m
                best_lane = in_lane
        if best_lane is None:
            return None
        # Find a green state that serves this lane.
        for g, pairs in self._served[tid].items():
            if any(inl == best_lane for inl, _ in pairs):
                return g
        return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def terms(self, tls_id: str) -> dict[str, float]:
        """The four signed terms of the current green."""
        ph = self.phases.get(tls_id)
        return dict(ph.last_terms) if ph else {}

    def score(self, tls_id: str) -> float:
        """The total score of the current green."""
        ph = self.phases.get(tls_id)
        return ph.last_total if ph else 0.0

    def explain(self, tls_id: str) -> dict:
        """Mode, reason, and every option with served streets, terms, total,
        and current/target flags."""
        ph = self.phases.get(tls_id)
        if ph is None:
            return {}
        options = []
        for o in ph.last_options:
            options.append({
                "state": o.state,
                "streets": o.streets,
                "terms": o.terms,
                "total": o.total,
                "current": o.is_current,
                "target": o.is_target,
            })
        return {
            "mode": "preempt" if ph.preempt else ph.mode,
            "reason": ph.reason,
            "options": options,
        }

    # ------------------------------------------------------------------
    # Street-name helper (best-effort from lane ids)
    # ------------------------------------------------------------------

    _lane_names: dict[str, str] = {}

    def set_lane_names(self, mapping: dict[str, str]) -> None:
        """Optional: map lane id -> street name for nicer explanations."""
        self._lane_names = dict(mapping)

    def _street_names(self, tid: str, in_lanes: list[str]) -> list[str]:
        names = []
        for l in in_lanes:
            nm = self._lane_names.get(l)
            if nm is None:
                # Fall back to the edge id (strip lane index).
                nm = l.rsplit("_", 1)[0] if "_" in l else l
            if nm not in names:
                names.append(nm)
        return names
