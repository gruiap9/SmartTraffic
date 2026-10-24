from __future__ import annotations

"""SumoRunner: drives one headless SUMO simulation and emits frames.

Several runners coexist in one process (one per strategy pane); each keeps its
own labelled TraCI connection and uses that connection object everywhere so the
simulations stay independent.
"""

import math
import os
import random
from typing import Optional

import sumolib
import traci
import traci.constants as tc

from core.types import LaneObs, TLSInfo, Weights

# Subscription variables we read per departed vehicle.
_SUB_VARS = [
    tc.VAR_POSITION,          # (x, y)
    tc.VAR_ANGLE,             # degrees, clockwise from north
    tc.VAR_TYPE,              # vType id
    tc.VAR_SPEED,
    tc.VAR_CO2EMISSION,       # mg/s
    tc.VAR_WAITING_TIME,
    tc.VAR_LANE_ID,
    tc.VAR_LANEPOSITION,
    tc.VAR_NOXEMISSION,       # mg/s
    tc.VAR_PMXEMISSION,       # mg/s
    tc.VAR_FUELCONSUMPTION,   # mg/s
]

HALT_SPEED = 0.1              # m/s below which a vehicle is "halted"
CHAIN_MAX_M = 250.0           # chain upstream/downstream up to this distance
CO2_SENTINEL = -1.0e8         # SUMO reports ~ -1e9 for teleporting vehicles

# vType id -> frame "kind" letter.
_KIND = {"car": "c", "bus": "b", "ambulance": "e", "crash": "x"}


def _kind_of(vtype: str) -> str:
    return _KIND.get(vtype, "c")


class SumoRunner:
    def __init__(
        self,
        label: str,
        cfg: str,
        seed: int,
        controller=None,
        tripinfo: Optional[str] = None,
        program: str = "0",
    ):
        self.label = label
        self.cfg = cfg
        self.seed = seed
        self.controller = controller
        self.tripinfo = tripinfo
        self.program = program

        self.t = 0
        self._closed = False

        # Per-vehicle state carried across steps.
        self._halted_prev: dict[str, bool] = {}     # for counting stops
        self._subscribed: set[str] = set()

        # Cumulative metrics.
        self._arrived = 0
        self._teleports = 0
        self._stops = 0
        self._halt_veh_s = 0.0      # integral of halting vehicles (for delay)
        self._veh_s = 0.0           # integral of vehicles present
        self._co2_mg = 0.0
        self._idle_co2_mg = 0.0
        self._fuel_mg = 0.0
        self._nox_mg = 0.0
        self._pm_mg = 0.0
        self._tls_wait_s = 0.0
        self._max_wait_s = 0.0

        # Ambulance tracking.
        self._amb_ids: list[str] = []
        self._amb_start: dict[str, int] = {}
        self._amb_last_trip_s = 0.0

        # Bus tracking.
        self._bus_start: dict[str, int] = {}
        self._bus_trips = 0
        self._bus_total_s = 0.0

        # Heatmap EMA per edge (g/s per 100 m).
        self._heat: dict[str, float] = {}

        # --- start SUMO ---
        bin_sumo = sumolib.checkBinary("sumo")
        cmd = [bin_sumo, "-c", cfg, "--no-step-log", "--no-warnings",
               "--seed", str(seed), "--step-length", "1"]
        if tripinfo:
            cmd += [
                "--tripinfo-output", tripinfo,
                "--tripinfo-output.write-unfinished", "true",
                "--device.emissions.probability", "1",
            ]
        traci.start(cmd, label=label)
        self.conn = traci.getConnection(label)

        # Static net for topology (lane chaining).
        net_dir = os.path.dirname(os.path.abspath(cfg))
        net_file = self._net_file_from_cfg(cfg)
        self.net = sumolib.net.readNet(os.path.join(net_dir, net_file))

        self._setup_tls()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    @staticmethod
    def _net_file_from_cfg(cfg: str) -> str:
        import xml.etree.ElementTree as ET
        root = ET.parse(cfg).getroot()
        nf = root.find(".//net-file")
        return nf.get("value") if nf is not None else "net.net.xml"

    def _setup_tls(self) -> None:
        """Discover controllable lights, build TLSInfo, set programs, and
        precompute lane chains."""
        self.tls_info: dict[str, TLSInfo] = {}
        self.controlled_in_lanes: set[str] = set()
        self.controlled_out_lanes: set[str] = set()
        # lane -> list of (observed_lane, extra_distance) chained segments
        self._in_chain: dict[str, list[str]] = {}
        self._out_chain: dict[str, list[str]] = {}

        all_ids = self.conn.trafficlight.getIDList()
        controllable = []
        for tid in all_ids:
            logics = self.conn.trafficlight.getAllProgramLogics(tid)
            prog0 = next((l for l in logics if l.programID == "0"), None)
            if prog0 is None:
                continue
            greens = []
            for ph in prog0.phases:
                st = ph.state
                if ("G" in st or "g" in st) and st not in greens:
                    greens.append(st)
            if len(greens) < 2:
                continue  # not controllable
            controllable.append(tid)

            links = self.conn.trafficlight.getControlledLinks(tid)
            link_pairs: list[tuple[str, str]] = []
            for lk in links:
                if lk:
                    in_lane, out_lane, _via = lk[0]
                    link_pairs.append((in_lane, out_lane))
                else:
                    link_pairs.append(("", ""))
            self.tls_info[tid] = TLSInfo(
                tls_id=tid,
                links=link_pairs,
                green_states=greens,
                initial_state=prog0.phases[0].state,
            )

            for in_lane, out_lane in link_pairs:
                if in_lane:
                    self.controlled_in_lanes.add(in_lane)
                if out_lane:
                    self.controlled_out_lanes.add(out_lane)

        # expected_tls assertion
        self._assert_expected_tls(len(controllable))

        # Program assignment: every controllable light runs `program`, or "0"
        # when a controller is attached (CivicSignal sets states directly).
        # Fringe lights (single green state) only have program "0" and are
        # left untouched.
        prog = "0" if self.controller is not None else self.program
        for tid in self.tls_info:
            avail = {l.programID
                     for l in self.conn.trafficlight.getAllProgramLogics(tid)}
            target = prog if prog in avail else "0"
            self.conn.trafficlight.setProgram(tid, target)

        # Register controlled lights with the controller.
        if self.controller is not None:
            for info in self.tls_info.values():
                self.controller.register(info)

        # Precompute lane chains (static topology).
        for lane in self.controlled_in_lanes:
            self._in_chain[lane] = self._chain(lane, upstream=True)
        for lane in self.controlled_out_lanes:
            self._out_chain[lane] = self._chain(lane, upstream=False)

        # Build observed-lane -> controlled base-lane(s) maps. A controlled
        # lane maps to itself; each chained segment also maps to the base, so
        # vehicles on advance segments report into the controlled lane's obs.
        # A lane may feed several observations.
        self._in_targets: dict[str, list[str]] = {}
        self._out_targets: dict[str, list[str]] = {}
        for base in self.controlled_in_lanes:
            self._in_targets.setdefault(base, []).append(base)
            for seg in self._in_chain.get(base, []):
                self._in_targets.setdefault(seg, []).append(base)
        for base in self.controlled_out_lanes:
            self._out_targets.setdefault(base, []).append(base)
            for seg in self._out_chain.get(base, []):
                self._out_targets.setdefault(seg, []).append(base)

    def _assert_expected_tls(self, n: int) -> None:
        import json
        meta_path = os.path.join(os.path.dirname(os.path.abspath(self.cfg)),
                                 "meta.json")
        if not os.path.exists(meta_path):
            return
        meta = json.load(open(meta_path))
        exp = meta.get("expected_tls")
        if exp is not None:
            assert n == exp, f"expected {exp} controllable TLS, found {n}"

    def _chain(self, lane_id: str, upstream: bool) -> list[str]:
        """Chain a controlled lane through non-signal nodes (up to CHAIN_MAX_M),
        matching lane index, so every chained segment reports into this lane's
        LaneObs. Returns the chained lane ids (excluding the controlled lane
        itself)."""
        out: list[str] = []
        try:
            lane = self.net.getLane(lane_id)
        except KeyError:
            return out
        idx = lane.getIndex()
        dist = 0.0
        cur = lane
        seen = {lane_id}
        while dist < CHAIN_MAX_M:
            edge = cur.getEdge()
            node = edge.getFromNode() if upstream else edge.getToNode()
            # Stop at signalized junctions.
            if node.getType() == "traffic_light" or \
                    node.getType() == "traffic_light_right_on_red":
                break
            # Candidate continuing edges through this node.
            if upstream:
                cand_edges = node.getIncoming()
            else:
                cand_edges = node.getOutgoing()
            nxt = None
            for e in cand_edges:
                if e.getID() == edge.getID():
                    continue
                # matching lane index if available
                lanes = e.getLanes()
                if not lanes:
                    continue
                pick = lanes[idx] if idx < len(lanes) else lanes[-1]
                if pick.getID() not in seen:
                    nxt = pick
                    break
            if nxt is None:
                break
            out.append(nxt.getID())
            seen.add(nxt.getID())
            dist += nxt.getLength()
            cur = nxt
        return out

    # ------------------------------------------------------------------
    # Step
    # ------------------------------------------------------------------

    def step(self) -> dict:
        self.conn.simulationStep()
        self.t = int(self.conn.simulation.getTime())

        # Subscribe newly departed vehicles.
        for vid in self.conn.simulation.getDepartedIDList():
            if vid not in self._subscribed:
                self.conn.vehicle.subscribe(vid, _SUB_VARS)
                self._subscribed.add(vid)
                vt = self._safe_type(vid)
                if vt == "ambulance":
                    self._amb_ids.append(vid)
                    self._amb_start[vid] = self.t
                elif vt == "bus":
                    self._bus_start[vid] = self.t

        # Arrivals / teleports accounting.
        for vid in self.conn.simulation.getArrivedIDList():
            self._arrived += 1
            self._subscribed.discard(vid)
            self._halted_prev.pop(vid, None)
            if vid in self._bus_start:
                self._bus_trips += 1
                self._bus_total_s += self.t - self._bus_start.pop(vid)
            if vid in self._amb_start:
                self._amb_last_trip_s = self.t - self._amb_start.pop(vid)
                if vid in self._amb_ids:
                    self._amb_ids.remove(vid)
        self._teleports += self.conn.simulation.getStartingTeleportNumber()

        subs = self.conn.vehicle.getAllSubscriptionResults()

        veh_rows: list[list] = []
        running = 0
        queue = 0
        step_halt_co2 = 0.0

        # Reset per-step lane observations for controlled lanes.
        lane_obs: dict[str, LaneObs] = {
            l: LaneObs() for l in self.controlled_in_lanes
        }
        lane_obs_out: dict[str, LaneObs] = {
            l: LaneObs() for l in self.controlled_out_lanes
        }

        # Map observed lane (incl. chained) -> controlled in/out lane(s).
        # Built lazily from precomputed chains.

        edge_co2: dict[str, float] = {}

        for vid, res in subs.items():
            x, y = res.get(tc.VAR_POSITION, (0.0, 0.0))
            angle = res.get(tc.VAR_ANGLE, 0.0)
            vtype = res.get(tc.VAR_TYPE, "car")
            speed = res.get(tc.VAR_SPEED, 0.0)
            co2 = res.get(tc.VAR_CO2EMISSION, 0.0)
            wait = res.get(tc.VAR_WAITING_TIME, 0.0)
            lane = res.get(tc.VAR_LANE_ID, "")
            nox = res.get(tc.VAR_NOXEMISSION, 0.0)
            pm = res.get(tc.VAR_PMXEMISSION, 0.0)
            fuel = res.get(tc.VAR_FUELCONSUMPTION, 0.0)

            # Teleporting vehicles report a huge negative CO2 sentinel; skip.
            if co2 < 0 or co2 == CO2_SENTINEL:
                continue

            running += 1
            halted = speed < HALT_SPEED
            kind = _kind_of(vtype)
            if kind == "c" and halted:
                pass  # colour decided on the client, but we pass halted flag
            veh_rows.append([vid, round(x, 1), round(y, 1),
                             round(angle, 1), kind, 1 if halted else 0])

            # Cumulative emissions.
            self._co2_mg += co2
            self._fuel_mg += fuel
            self._nox_mg += nox
            self._pm_mg += pm
            self._veh_s += 1.0
            if halted:
                queue += 1
                self._halt_veh_s += 1.0
                self._idle_co2_mg += co2
                step_halt_co2 += co2
            if wait > self._max_wait_s:
                self._max_wait_s = wait

            # Stops: moving -> halted transition.
            was = self._halted_prev.get(vid, False)
            if halted and not was:
                self._stops += 1
            self._halted_prev[vid] = halted

            # Heatmap edge accumulation (CO2 g/s).
            if lane and "_" in lane:
                eid = lane.rsplit("_", 1)[0]
                if not eid.startswith(":"):
                    edge_co2[eid] = edge_co2.get(eid, 0.0) + co2 / 1000.0

            # Lane observations (controlled + chained).
            self._record_lane_obs(lane_obs, lane_obs_out, lane, vtype,
                                   halted, speed, co2, wait)

        # tls_wait: waiting time accumulated on controlled in-lanes this step.
        tls_wait_step = sum(o.max_wait_s for o in lane_obs.values())
        self._tls_wait_s += 0.0  # kept as cumulative queue proxy below

        # Ambulance corridor: set emergency_dist_m on lights ahead.
        self._apply_corridor(lane_obs)

        # Controller control.
        sig = self._control(lane_obs, lane_obs_out)

        # Emissions heatmap EMA (g/s per 100 m).
        self._update_heat(edge_co2)

        # Ambulance metrics.
        amb_active = len(self._amb_ids)
        amb_elapsed = 0
        if self._amb_ids:
            amb_elapsed = self.t - min(self._amb_start.get(a, self.t)
                                       for a in self._amb_ids)

        m = self._metrics(running, queue, amb_active, amb_elapsed)

        frame_sim = {
            "veh": veh_rows,
            "sig": sig,
            "m": m,
            "heat": {e: round(v, 3) for e, v in self._heat.items()},
        }
        return frame_sim

    # ------------------------------------------------------------------
    # Lane observations
    # ------------------------------------------------------------------

    def _record_lane_obs(self, lane_obs, lane_obs_out, lane, vtype,
                         halted, speed, co2, wait) -> None:
        if not lane:
            return
        is_bus = (vtype == "bus")
        # Direct controlled in-lane.
        targets_in = self._in_targets.get(lane, [])
        targets_out = self._out_targets.get(lane, [])
        for base in targets_in:
            o = lane_obs.get(base)
            if o is None:
                continue
            self._accumulate_obs(o, halted, is_bus, speed, co2, wait)
        for base in targets_out:
            o = lane_obs_out.get(base)
            if o is None:
                continue
            self._accumulate_obs(o, halted, is_bus, speed, co2, wait)

    @staticmethod
    def _accumulate_obs(o: LaneObs, halted, is_bus, speed, co2, wait) -> None:
        if halted:
            o.halting += 1
            o.co2_mg_s += co2
            if is_bus:
                o.buses += 1
        else:
            o.approaching += 1
            if is_bus:
                o.buses_moving += 1
        if wait > o.max_wait_s:
            o.max_wait_s = wait

    # ------------------------------------------------------------------
    # Corridor / control
    # ------------------------------------------------------------------

    def _apply_corridor(self, lane_obs: dict[str, LaneObs]) -> None:
        for amb in self._amb_ids:
            try:
                nxt = self.conn.vehicle.getNextTLS(amb)
            except traci.TraCIException:
                continue
            for (tlsID, linkIdx, dist, _state) in nxt:
                info = self.tls_info.get(tlsID)
                if info is None:
                    continue
                if 0 <= linkIdx < len(info.links):
                    in_lane = info.links[linkIdx][0]
                    o = lane_obs.get(in_lane)
                    if o is not None:
                        if o.emergency_dist_m is None or dist < o.emergency_dist_m:
                            o.emergency_dist_m = dist

    def _control(self, lane_obs, lane_obs_out) -> dict:
        """Apply controller states and return the per-lane signal colours."""
        sig: dict[str, str] = {}
        for tid, info in self.tls_info.items():
            if self.controller is not None:
                # Lanes view keyed by lane id, covering both in- and out-lanes
                # so the controller can read out-lane backpressure via links.
                lanes_view: dict[str, LaneObs] = {}
                for (in_lane, out_lane) in info.links:
                    if in_lane and in_lane not in lanes_view:
                        lanes_view[in_lane] = lane_obs.get(in_lane, LaneObs())
                    if out_lane and out_lane not in lanes_view:
                        lanes_view[out_lane] = lane_obs_out.get(out_lane, LaneObs())
                state = self.controller.step(tid, self.t, lanes_view)
                cur = self.conn.trafficlight.getRedYellowGreenState(tid)
                if state and state != cur:
                    self.conn.trafficlight.setRedYellowGreenState(tid, state)
                self._emit_sig(sig, info, state or cur)
            else:
                cur = self.conn.trafficlight.getRedYellowGreenState(tid)
                self._emit_sig(sig, info, cur)
        return sig

    @staticmethod
    def _emit_sig(sig: dict, info: TLSInfo, state: str) -> None:
        for i, (in_lane, _out) in enumerate(info.links):
            if not in_lane or i >= len(state):
                continue
            ch = state[i]
            col = "G" if ch in "Gg" else ("y" if ch in "yY" else "r")
            # Strongest colour wins per lane (G > y > r).
            prev = sig.get(in_lane)
            if prev is None or _sig_rank(col) > _sig_rank(prev):
                sig[in_lane] = col

    # ------------------------------------------------------------------
    # Heat / metrics
    # ------------------------------------------------------------------

    def _update_heat(self, edge_co2: dict[str, float]) -> None:
        alpha = 0.05
        touched = set(edge_co2)
        for eid, g_s in edge_co2.items():
            length = self._edge_len(eid)
            density = g_s / (length / 100.0) if length > 0 else g_s
            prev = self._heat.get(eid, 0.0)
            self._heat[eid] = prev + alpha * (density - prev)
        # Decay edges with no emission this step.
        for eid in list(self._heat):
            if eid not in touched:
                self._heat[eid] = self._heat[eid] * (1 - alpha)
                if self._heat[eid] < 1e-3:
                    del self._heat[eid]

    def _edge_len(self, eid: str) -> float:
        try:
            return self.net.getEdge(eid).getLength()
        except KeyError:
            return 0.0

    def _metrics(self, running, queue, amb_active, amb_elapsed) -> dict:
        denom = self._arrived + running
        avg_delay = (self._halt_veh_s / denom) if denom else 0.0
        return {
            "t": self.t,
            "running": running,
            "arrived": self._arrived,
            "avg_delay_s": round(avg_delay, 2),
            "queue": queue,
            "co2_kg": round(self._co2_mg / 1e6, 4),
            "idle_co2_kg": round(self._idle_co2_mg / 1e6, 4),
            "max_wait_s": round(self._max_wait_s, 1),
            "teleports": self._teleports,
            "amb_active": amb_active,
            "amb_elapsed_s": amb_elapsed,
            "amb_last_trip_s": round(self._amb_last_trip_s, 1),
            "tls_wait": round(self._tls_wait_s, 1),
            "halt_vs": round(self._halt_veh_s, 1),
            "veh_s": round(self._veh_s, 1),
            "stops": self._stops,
            "fuel_l": round(self._fuel_mg / 745000.0, 4),
            "nox_g": round(self._nox_mg / 1000.0, 3),
            "pm_g": round(self._pm_mg / 1000.0, 4),
            "bus_trips": self._bus_trips,
            "bus_avg_trip_s": round(self._bus_total_s / self._bus_trips, 1)
                              if self._bus_trips else 0.0,
        }

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def spawn_ambulance(self, route_id: str = "amb_route",
                        vid: Optional[str] = None) -> str:
        if vid is None:
            vid = f"amb_{self.t}"
        try:
            self.conn.vehicle.add(vid, route_id, typeID="ambulance",
                                  departLane="best", departSpeed="max")
        except traci.TraCIException:
            # Route id may be missing; try building from meta ambulance.
            pass
        return vid

    def surge(self, from_edges: list[str], to_edges: list[str], n: int,
              over: int, k: int = 0) -> None:
        """Add n cars via findRoute, deterministic across sims (seed 1000+k)."""
        rng = random.Random(1000 + k)
        if not from_edges or not to_edges:
            return
        for i in range(n):
            fe = rng.choice(from_edges)
            te = rng.choice(to_edges)
            depart = self.t + (i * over) // max(1, n)
            vid = f"surge_{self.t}_{i}"
            try:
                route = self.conn.simulation.findRoute(fe, te)
                if not route.edges:
                    continue
                rid = f"r_{vid}"
                self.conn.route.add(rid, route.edges)
                self.conn.vehicle.add(vid, rid, typeID="car",
                                      depart=str(depart),
                                      departLane="best", departSpeed="max")
            except traci.TraCIException:
                continue

    def crash(self, edge: str, lanes: list[int], pos: float,
              duration: int) -> None:
        """Stop crash vehicles on the given lanes for `duration` seconds."""
        for ln in lanes:
            lane_id = f"{edge}_{ln}"
            vid = f"crash_{self.t}_{ln}"
            try:
                rid = f"cr_{vid}"
                self.conn.route.add(rid, [edge])
                self.conn.vehicle.add(vid, rid, typeID="crash",
                                      departLane=str(ln), departPos=str(pos),
                                      departSpeed="0")
                self.conn.vehicle.setStop(vid, edge, pos=pos, laneIndex=ln,
                                          duration=duration)
            except traci.TraCIException:
                continue

    # ------------------------------------------------------------------
    # Inspection / follow
    # ------------------------------------------------------------------

    def inspect(self, tls_id: str) -> dict:
        info = self.tls_info.get(tls_id)
        if info is None:
            return {}
        out = {
            "tls": tls_id,
            "state": self.conn.trafficlight.getRedYellowGreenState(tls_id),
            "program": self.conn.trafficlight.getProgram(tls_id),
        }
        if self.controller is not None and hasattr(self.controller, "explain"):
            out["explain"] = self.controller.explain(tls_id)
        else:
            # Baseline: next switch time.
            try:
                out["next_switch"] = self.conn.trafficlight.getNextSwitch(tls_id) - self.t
            except traci.TraCIException:
                out["next_switch"] = None
        return out

    def trip(self, vid: str) -> dict:
        try:
            depart = self.conn.vehicle.getDeparture(vid)
        except traci.TraCIException:
            depart = None
        present = vid in self._subscribed
        return {
            "vid": vid,
            "depart": depart,
            "present": present,
            "arrived": (not present) and depart is not None,
            "elapsed": (self.t - depart) if depart is not None else None,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_type(self, vid: str) -> str:
        try:
            return self.conn.vehicle.getTypeID(vid)
        except traci.TraCIException:
            return "car"


def _sig_rank(col: str) -> int:
    return {"r": 0, "y": 1, "G": 2}.get(col, 0)
