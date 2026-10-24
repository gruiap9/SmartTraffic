from __future__ import annotations

"""Controller tests with a fake 4-link light (no SUMO).

The fake light has four links, two per axis:
  links[0], links[1] = east-west  (served by green state "GGrr")
  links[2], links[3] = north-south (served by green state "rrGG")
"""

import pytest

from core.controller import (
    WeightedMaxPressure, BALANCED,
    MIN_GREEN, MAX_GREEN, YELLOW, ALL_RED, HYST, PREEMPT_DIST_M,
)
from core.types import LaneObs, TLSInfo, Weights

EW = "GGrr"   # serves in-lanes ew0, ew1
NS = "rrGG"   # serves in-lanes ns0, ns1


def make_light() -> TLSInfo:
    return TLSInfo(
        tls_id="X",
        links=[
            ("ew0", "ewo0"),
            ("ew1", "ewo1"),
            ("ns0", "nso0"),
            ("ns1", "nso1"),
        ],
        green_states=[EW, NS],
        initial_state=EW,
    )


def obs(**kw) -> LaneObs:
    return LaneObs(**kw)


def empty_lanes() -> dict[str, LaneObs]:
    return {l: LaneObs() for l in
            ("ew0", "ew1", "ns0", "ns1", "ewo0", "ewo1", "nso0", "nso1")}


def run_until(ctrl, lanes, t_start, steps):
    """Step the controller `steps` times starting at t_start; return states."""
    states = []
    for i in range(steps):
        s = ctrl.step("X", t_start + i, lanes)
        states.append(s)
    return states


# ----------------------------------------------------------------------
# 1. A heavy queue wins.
# ----------------------------------------------------------------------

def test_heavy_queue_wins():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # Heavy queue on NS, light on EW.
    lanes["ns0"] = obs(halting=20)
    lanes["ns1"] = obs(halting=18)
    lanes["ew0"] = obs(halting=1)
    # Start on EW; after min-green it should switch toward NS.
    states = run_until(ctrl, lanes, 0, MIN_GREEN + 2)
    # First MIN_GREEN steps hold EW.
    assert states[0] == EW
    assert states[MIN_GREEN - 1] == EW
    # Then it transitions (yellow appears).
    assert "y" in states[MIN_GREEN]
    # NS is the higher-scoring option.
    exp = ctrl.explain("X")
    ns_opt = next(o for o in exp["options"] if o["state"] == NS)
    ew_opt = next(o for o in exp["options"] if o["state"] == EW)
    assert ns_opt["total"] > ew_opt["total"]


# ----------------------------------------------------------------------
# 2. Min-green is respected.
# ----------------------------------------------------------------------

def test_min_green_respected():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    lanes["ns0"] = obs(halting=50)   # huge pressure to switch
    for i in range(MIN_GREEN):
        s = ctrl.step("X", i, lanes)
        assert s == EW, f"switched before min-green at t={i}"
    # At exactly MIN_GREEN it may begin transition.
    s = ctrl.step("X", MIN_GREEN, lanes)
    assert "y" in s


# ----------------------------------------------------------------------
# 3. Hysteresis holds.
# ----------------------------------------------------------------------

def test_hysteresis_holds():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # EW current = 3 halting; NS = 4 halting -> lead of 1 < HYST(2) -> hold.
    lanes["ew0"] = obs(halting=3)
    lanes["ns0"] = obs(halting=4)
    states = run_until(ctrl, lanes, 0, MIN_GREEN + 5)
    assert all(s == EW for s in states), "switched despite lead within hysteresis"


def test_switches_when_lead_exceeds_hysteresis():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    lanes["ew0"] = obs(halting=3)
    lanes["ns0"] = obs(halting=8)   # lead of 5 > HYST
    states = run_until(ctrl, lanes, 0, MIN_GREEN + 2)
    assert "y" in states[MIN_GREEN]


# ----------------------------------------------------------------------
# 4. Yellow and all-red precede every change.
# ----------------------------------------------------------------------

def test_yellow_then_allred_then_green():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    lanes["ns0"] = obs(halting=30)
    t = 0
    # Hold through min-green.
    for i in range(MIN_GREEN):
        ctrl.step("X", t, lanes); t += 1
    # Begin transition.
    s = ctrl.step("X", t, lanes); t += 1
    assert "y" in s and "G" not in s.replace("y", ""), "expected yellow"
    # Yellow lasts YELLOW seconds.
    for _ in range(YELLOW - 1):
        s = ctrl.step("X", t, lanes); t += 1
        assert "y" in s
    # Then all-red.
    s = ctrl.step("X", t, lanes); t += 1
    assert set(s) == {"r"}, f"expected all-red, got {s}"
    for _ in range(ALL_RED - 1):
        s = ctrl.step("X", t, lanes); t += 1
        assert set(s) == {"r"}
    # Then the new green (NS).
    s = ctrl.step("X", t, lanes); t += 1
    assert s == NS


# ----------------------------------------------------------------------
# 5. Preemption ignores min-green but not yellow.
# ----------------------------------------------------------------------

def test_preempt_ignores_min_green_but_not_yellow():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, preempt=True))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # No ambulance yet: holds EW through a couple of steps (well inside
    # min-green, nothing forces a switch).
    assert ctrl.step("X", 0, lanes) == EW
    assert ctrl.step("X", 1, lanes) == EW
    # Ambulance now approaching on NS, well inside range, still < min-green.
    lanes["ns0"] = obs(emergency_dist_m=120.0)
    s = ctrl.step("X", 2, lanes)
    # Begins switching toward NS immediately, ignoring min-green...
    assert "y" in s, "preemption should switch before min-green"
    # ...but still goes through yellow, not straight to green.
    assert "G" not in s.replace("y", ""), "preemption must not skip yellow"
    # The yellow runs its course, then all-red, then the NS green.
    ctrl.step("X", 3, lanes)        # yellow continues
    ctrl.step("X", 4, lanes)        # yellow continues (YELLOW=3)
    s_red = ctrl.step("X", 5, lanes)
    assert set(s_red) == {"r"}, "all-red after yellow"


# ----------------------------------------------------------------------
# 6. Preemption out of range or turned off does nothing.
# ----------------------------------------------------------------------

def test_preempt_out_of_range_noop():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, preempt=True))
    ctrl.register(make_light())
    lanes = empty_lanes()
    lanes["ns0"] = obs(emergency_dist_m=PREEMPT_DIST_M + 50)  # out of range
    states = run_until(ctrl, lanes, 0, MIN_GREEN)
    assert all(s == EW for s in states)


def test_preempt_disabled_noop():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, preempt=False))
    ctrl.register(make_light())
    lanes = empty_lanes()
    lanes["ns0"] = obs(emergency_dist_m=50.0)   # in range but preempt off
    states = run_until(ctrl, lanes, 0, MIN_GREEN)
    assert all(s == EW for s in states), "preempt disabled should not switch early"


# ----------------------------------------------------------------------
# 7. Preemption holds green past max-green.
# ----------------------------------------------------------------------

def test_preempt_holds_past_max_green():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, preempt=True))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # Ambulance approaching on the CURRENT (EW) green the whole time.
    lanes["ew0"] = obs(emergency_dist_m=200.0)
    # Also pile pressure on NS so without preempt it would rotate at max-green.
    lanes["ns0"] = obs(halting=40)
    states = run_until(ctrl, lanes, 0, MAX_GREEN + 10)
    # Even past MAX_GREEN, EW green holds for the ambulance.
    assert states[MAX_GREEN + 5] == EW, "should hold green for ambulance past max-green"


# ----------------------------------------------------------------------
# 8. Max-green rotates.
# ----------------------------------------------------------------------

def test_max_green_rotates():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # Keep EW the best so it never switches on score; only max-green forces it.
    lanes["ew0"] = obs(halting=10)
    lanes["ns0"] = obs(halting=1)
    saw_yellow_at = None
    for i in range(MAX_GREEN + 3):
        s = ctrl.step("X", i, lanes)
        if "y" in s and saw_yellow_at is None:
            saw_yellow_at = i
    assert saw_yellow_at is not None, "max-green should force a rotation"
    assert saw_yellow_at >= MAX_GREEN, f"rotated too early at {saw_yellow_at}"


# ----------------------------------------------------------------------
# 9. Weights change live.
# ----------------------------------------------------------------------

def test_weights_change_live():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, transit=0.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # Buses waiting on NS; with transit=0 EW (empty) ties/zero, NS also near 0.
    lanes["ns0"] = obs(buses=3)
    lanes["ew0"] = obs(halting=1)
    # With flow only, EW (halting 1) slightly beats NS (halting 0).
    run_until(ctrl, lanes, 0, MIN_GREEN)
    exp_before = ctrl.explain("X")
    ns_before = next(o for o in exp_before["options"] if o["state"] == NS)["total"]
    # Now crank transit live.
    ctrl.set_weights(Weights(flow=1.0, transit=2.0))
    ctrl.step("X", MIN_GREEN, lanes)
    exp_after = ctrl.explain("X")
    ns_after = next(o for o in exp_after["options"] if o["state"] == NS)["total"]
    assert ns_after > ns_before, "live weight change should raise NS transit score"


# ----------------------------------------------------------------------
# 10. Out-lane backpressure.
# ----------------------------------------------------------------------

def test_out_lane_backpressure():
    ctrl = WeightedMaxPressure(Weights(flow=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # NS has a big queue, but its OUT lanes are jammed too -> pressure reduced.
    lanes["ns0"] = obs(halting=10)
    lanes["ns1"] = obs(halting=10)
    lanes["nso0"] = obs(halting=9)
    lanes["nso1"] = obs(halting=9)
    # EW modest queue, clear out lanes.
    lanes["ew0"] = obs(halting=5)
    run_until(ctrl, lanes, 0, MIN_GREEN)
    exp = ctrl.explain("X")
    ns = next(o for o in exp["options"] if o["state"] == NS)
    ew = next(o for o in exp["options"] if o["state"] == EW)
    # NS pressure = (10-9)+(10-9)=2; EW = 5. EW wins despite smaller raw queue.
    assert ew["total"] > ns["total"], "backpressure should discount jammed exits"


# ----------------------------------------------------------------------
# 11. The climate term protects a moving platoon.
# ----------------------------------------------------------------------

def test_climate_protects_moving_platoon():
    ctrl = WeightedMaxPressure(Weights(flow=1.0, climate=1.0))
    ctrl.register(make_light())
    lanes = empty_lanes()
    # EW: a moving platoon (approaching), few halted.
    lanes["ew0"] = obs(halting=1, approaching=12)
    # NS: a modest standing queue.
    lanes["ns0"] = obs(halting=4)
    run_until(ctrl, lanes, 0, MIN_GREEN + 2)
    exp = ctrl.explain("X")
    ew = next(o for o in exp["options"] if o["state"] == EW)
    ns = next(o for o in exp["options"] if o["state"] == NS)
    # Climate term rewards EW's approaching platoon, keeping it green.
    assert ew["terms"]["climate"] > 0
    assert ew["total"] > ns["total"], "climate should protect the moving platoon"
