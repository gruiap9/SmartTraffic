from __future__ import annotations

"""Scenario builder for CivicSignal.

Usage:
    python scenario/build.py [grid|midtown]

Builds a SUMO scenario under scenario/<id>/ and writes the frontend
map description to web/scenarios/<id>.json.

This module holds shared helpers plus the grid scenario. The midtown
scenario (OpenStreetMap) lives alongside and shares the helpers.
"""

import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import sumolib

# --------------------------------------------------------------------------
# Paths / binaries
# --------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCEN_DIR = os.path.join(ROOT, "scenario")
WEB_SCEN_DIR = os.path.join(ROOT, "web", "scenarios")


def _bin(name: str) -> str:
    return sumolib.checkBinary(name)


def _run(cmd: list[str]) -> None:
    print("  $", " ".join(os.path.basename(c) if i == 0 else c
                           for i, c in enumerate(cmd)))
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


# --------------------------------------------------------------------------
# Shared net introspection helpers
# --------------------------------------------------------------------------

def read_net(net_path: str) -> sumolib.net.Net:
    return sumolib.net.readNet(net_path, withPrograms=True, withConnections=True)


def tls_green_states(net_path: str) -> dict[str, list[str]]:
    """Return {tls_id: [distinct green phase states]} from program '0'."""
    tree = ET.parse(net_path)
    out: dict[str, list[str]] = {}
    for tl in tree.getroot().findall("tlLogic"):
        if tl.get("programID") != "0":
            continue
        greens: list[str] = []
        for p in tl.findall("phase"):
            st = p.get("state", "")
            if ("G" in st or "g" in st) and st not in greens:
                greens.append(st)
        out[tl.get("id")] = greens
    return out


def controllable_tls(net_path: str) -> list[str]:
    """Lights with at least 2 distinct green states."""
    return sorted(tid for tid, g in tls_green_states(net_path).items() if len(g) >= 2)


def tls_coord(net: sumolib.net.Net, tls_id: str) -> tuple[float, float]:
    """A representative coordinate for a traffic light.

    Works for plain nodes (grid) and joined clusters (midtown): falls back to
    the centroid of the stop-line ends of the controlled lanes.
    """
    try:
        return net.getNode(tls_id).getCoord()
    except KeyError:
        pass
    tl = {t.getID(): t for t in net.getTrafficLights()}.get(tls_id)
    if tl is None:
        return (0.0, 0.0)
    pts = []
    for c in tl.getConnections():
        from_lane = c[0]
        shp = from_lane.getShape()
        if shp:
            pts.append(shp[-1])
    if not pts:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def tls_phases(net_path: str, tls_id: str) -> list[tuple[int, str]]:
    tree = ET.parse(net_path)
    for tl in tree.getroot().findall("tlLogic"):
        if tl.get("id") == tls_id and tl.get("programID") == "0":
            return [(int(float(p.get("duration"))), p.get("state"))
                    for p in tl.findall("phase")]
    return []


# --------------------------------------------------------------------------
# TLS additional-program generation (actuated + greenwave)
# --------------------------------------------------------------------------

@dataclass
class TLSGeom:
    tls_id: str
    x: float
    y: float
    # direction of the major road through this light (unit vector), or None
    major_dir: Optional[tuple[float, float]] = None


def _phase_is_green(state: str) -> bool:
    return "G" in state or "g" in state


def build_tls_add(
    net_path: str,
    out_path: str,
    geoms: dict[str, TLSGeom],
    major_speed: float = 10.0,
    major_green: int = 52,
    minor_green: int = 32,
    yellow: int = 3,
) -> None:
    """Write tls.add.xml with 'actuated' and 'greenwave' programs for every
    controllable light (>= 2 distinct green states).

    - actuated: same phases as program 0, type=actuated, greens minDur 6 /
      maxDur 55, max-gap 3.
    - greenwave: static. Major green `major_green` s / yellow / minor green
      `minor_green` s / yellow, with offset = (position . major_dir) / speed,
      mod cycle.
    """
    ctrl = set(controllable_tls(net_path))
    cycle = major_green + yellow + minor_green + yellow

    root = ET.Element("additional")
    for tid in sorted(ctrl):
        phases0 = tls_phases(net_path, tid)
        if not phases0:
            continue

        # --- actuated: mirror program 0 structure ---
        act = ET.SubElement(root, "tlLogic", id=tid, type="actuated",
                            programID="actuated", offset="0")
        act.set("tl", tid)
        ET.SubElement(act, "param", key="max-gap", value="3")
        for dur, state in phases0:
            ph = ET.SubElement(act, "phase", duration=str(dur), state=state)
            if _phase_is_green(state):
                ph.set("minDur", "6")
                ph.set("maxDur", "55")

        # --- greenwave: static, first two greens get major/minor timing ---
        geom = geoms.get(tid)
        offset = 0.0
        if geom and geom.major_dir is not None:
            dx, dy = geom.major_dir
            proj = geom.x * dx + geom.y * dy
            offset = (proj / major_speed) % cycle

        gw = ET.SubElement(root, "tlLogic", id=tid, type="static",
                           programID="greenwave", offset=f"{offset:.1f}")
        green_phases = [(d, s) for d, s in phases0 if _phase_is_green(s)]
        yellow_phases = [(d, s) for d, s in phases0 if not _phase_is_green(s)]
        # Reconstruct in original order, substituting durations.
        gi = 0
        for dur, state in phases0:
            if _phase_is_green(state):
                g = major_green if gi == 0 else minor_green
                ET.SubElement(gw, "phase", duration=str(g), state=state)
                gi += 1
            else:
                ET.SubElement(gw, "phase", duration=str(yellow), state=state)

    _write_xml(root, out_path)


# --------------------------------------------------------------------------
# XML writing
# --------------------------------------------------------------------------

def _indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + "    " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "    "
        for child in elem:
            _indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = pad + "    "
        if not child.tail or not child.tail.strip():
            child.tail = pad
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = pad


def _write_xml(root: ET.Element, path: str) -> None:
    _indent(root)
    tree = ET.ElementTree(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


# --------------------------------------------------------------------------
# Fringe detection
# --------------------------------------------------------------------------

def fringe_nodes(net: sumolib.net.Net) -> list[str]:
    """Dead-end nodes: exactly one distinct neighbour."""
    out = []
    for n in net.getNodes():
        nbrs = set()
        for e in n.getIncoming() + n.getOutgoing():
            nbrs.add(e.getFromNode().getID())
            nbrs.add(e.getToNode().getID())
        nbrs.discard(n.getID())
        if len(nbrs) == 1:
            out.append(n.getID())
    return out


def fringe_source_edges(net: sumolib.net.Net) -> list[str]:
    """Edges leaving a fringe node (sources)."""
    out = []
    for nid in fringe_nodes(net):
        n = net.getNode(nid)
        for e in n.getOutgoing():
            out.append(e.getID())
    return sorted(set(out))


def fringe_sink_edges(net: sumolib.net.Net) -> list[str]:
    """Edges arriving at a fringe node (sinks)."""
    out = []
    for nid in fringe_nodes(net):
        n = net.getNode(nid)
        for e in n.getIncoming():
            out.append(e.getID())
    return sorted(set(out))


# --------------------------------------------------------------------------
# vTypes (shared)
# --------------------------------------------------------------------------

VTYPES = """\
    <vType id="car" vClass="passenger" length="4.5" color="0.8,0.8,0.85"/>
    <vType id="bus" vClass="bus" length="12" color="0.2,0.5,1"/>
    <vType id="ambulance" vClass="emergency" speedFactor="1.3" color="1,0.1,0.1" guiShape="emergency"/>
    <vType id="crash" vClass="passenger" length="4.5" color="1,0.55,0" impatience="0"/>
"""


# --------------------------------------------------------------------------
# GRID scenario
# --------------------------------------------------------------------------

def build_grid() -> None:
    sid = "grid"
    out = os.path.join(SCEN_DIR, sid)
    os.makedirs(out, exist_ok=True)
    net_path = os.path.join(out, "net.net.xml")

    print(f"[grid] generating net -> {net_path}")
    _run([
        _bin("netgenerate"),
        "--grid", "--grid.number=3", "--grid.length=200",
        "--grid.attach-length=200", "--default.lanenumber=2",
        "-j", "traffic_light",
        "-o", net_path,
    ])

    net = read_net(net_path)
    ctrl = controllable_tls(net_path)
    print(f"[grid] controllable lights: {len(ctrl)} -> {ctrl}")

    sources = fringe_source_edges(net)
    sinks = fringe_sink_edges(net)
    print(f"[grid] sources={len(sources)} sinks={len(sinks)}")

    # Classify fringe edges by compass side using node coords.
    def edge_side(eid: str, outgoing: bool) -> str:
        e = net.getEdge(eid)
        node = e.getFromNode() if outgoing else e.getToNode()
        x, y = node.getCoord()
        if x <= 0:
            return "W"
        if x >= 800:
            return "E"
        if y <= 0:
            return "S"
        return "N"

    src_by_side: dict[str, list[str]] = {"N": [], "S": [], "E": [], "W": []}
    for e in sources:
        src_by_side[edge_side(e, True)].append(e)
    sink_by_side: dict[str, list[str]] = {"N": [], "S": [], "E": [], "W": []}
    for e in sinks:
        sink_by_side[edge_side(e, False)].append(e)

    # Main axis = east-west (enters from W and E). Cross street = north-south.
    _build_grid_routes(out, src_by_side, sink_by_side, net)

    # TLS geometry for greenwave offsets: major road is east-west -> dir (1,0).
    geoms: dict[str, TLSGeom] = {}
    for tid in ctrl:
        node = net.getNode(tid)
        x, y = node.getCoord()
        geoms[tid] = TLSGeom(tid, x, y, major_dir=(1.0, 0.0))

    tls_add = os.path.join(out, "tls.add.xml")
    build_tls_add(net_path, tls_add, geoms, major_speed=10.0)
    print(f"[grid] wrote {tls_add}")

    # sumocfg
    cfg_path = os.path.join(out, "sim.sumocfg")
    _write_sumocfg(cfg_path, "net.net.xml", ["routes.rou.xml"],
                   ["tls.add.xml"], end=2400)

    # meta.json
    amb_label = "Ambulance \u2192 right-top"
    meta = {
        "id": sid,
        "title": "Demo Grid (3\u00d73)",
        "cfg": os.path.relpath(cfg_path, ROOT),
        "rush": [600, 1800],
        "end": 2400,
        "expected_tls": 9,
        "rotate_deg": 0,
        "ambulance": {
            "label": amb_label,
            "route": "amb_route",
        },
        "surge": {
            "label": "Rush surge",
            "from": src_by_side["W"],
            "to": sink_by_side["E"],
            "n": 120,
            "over": 240,
        },
        "crash": {
            "label": "Crash in the grid",
            "edge": _middle_edge(net),
            "lanes": [0, 1],
            "pos": 100.0,
            "duration": 300,
        },
        "landmarks": [
            {"name": "City Hall", "x": 400, "y": 400},
            {"name": "Market", "x": 200, "y": 600},
        ],
    }
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"[grid] wrote meta.json")

    # web map description
    _write_web_scenario(sid, net_path, ctrl, meta)
    print(f"[grid] wrote web/scenarios/{sid}.json")

    # Final check
    assert len(ctrl) == 9, f"expected 9 controllable lights, got {len(ctrl)}"
    print(f"[grid] OK: {len(ctrl)} controllable lights")


def _middle_edge(net: sumolib.net.Net) -> str:
    """An interior edge on the middle row (B1-centric) for the crash."""
    for cand in ("A1B1", "B1C1", "B1B2"):
        if net.hasEdge(cand):
            return cand
    # Fallback: first interior edge.
    for e in net.getEdges():
        if not e.getID().startswith(":"):
            return e.getID()
    return ""


def _build_grid_routes(
    out: str,
    src_by_side: dict[str, list[str]],
    sink_by_side: dict[str, list[str]],
    net: sumolib.net.Net,
) -> None:
    """Probability flows + bus flow + ambulance route + crash vtype.

    Demand (per entry edge):
      0-600s:   main (E-W) 250 veh/h,  cross (N-S) 150
      600-1800: rush       750,                    250
      1800-2400:           250,                    150
    80% straight to opposite fringe; 10% to each cross exit.
    """
    root = ET.Element("routes")
    # vTypes
    vt = ET.fromstring("<routes>" + VTYPES + "</routes>")
    for child in vt:
        root.append(child)

    periods = [
        (0, 600, 250, 150),
        (600, 1800, 750, 250),
        (1800, 2400, 250, 150),
    ]

    # Opposite-side map for "straight to opposite fringe".
    opposite = {"W": "E", "E": "W", "N": "S", "S": "N"}
    cross_of = {"W": ("N", "S"), "E": ("N", "S"),
                "N": ("E", "W"), "S": ("E", "W")}

    fid = 0

    # ElementTree can't take 'from' as a kwarg, so flows are built via _flow().
    for (b, e, main_h, cross_h) in periods:
        for side in ("W", "E"):   # main axis
            for src in src_by_side[side]:
                opp = opposite[side]
                dst_main = _aligned_sink(net, src, sink_by_side[opp])
                c1, c2 = cross_of[side]
                dst_c1 = _nearest_sink(net, src, sink_by_side[c1])
                dst_c2 = _nearest_sink(net, src, sink_by_side[c2])
                _flow(root, f"f{fid}", src, dst_main, b, e, main_h * 0.8); fid += 1
                if dst_c1:
                    _flow(root, f"f{fid}", src, dst_c1, b, e, main_h * 0.1); fid += 1
                if dst_c2:
                    _flow(root, f"f{fid}", src, dst_c2, b, e, main_h * 0.1); fid += 1
        for side in ("N", "S"):   # cross street
            for src in src_by_side[side]:
                opp = opposite[side]
                dst_main = _aligned_sink(net, src, sink_by_side[opp])
                c1, c2 = cross_of[side]
                dst_c1 = _nearest_sink(net, src, sink_by_side[c1])
                dst_c2 = _nearest_sink(net, src, sink_by_side[c2])
                _flow(root, f"f{fid}", src, dst_main, b, e, cross_h * 0.8); fid += 1
                if dst_c1:
                    _flow(root, f"f{fid}", src, dst_c1, b, e, cross_h * 0.1); fid += 1
                if dst_c2:
                    _flow(root, f"f{fid}", src, dst_c2, b, e, cross_h * 0.1); fid += 1

    # Bus flow along the middle row (y=400: left1 -> ... -> right1) every 120s.
    bus_from, bus_to = _row_edges(net, y=400)
    if bus_from and bus_to:
        f = ET.SubElement(root, "flow", id="bus_mid", type="bus",
                          begin="0", end="2400", period="120",
                          departLane="best", departSpeed="max")
        f.set("from", bus_from)
        f.set("to", bus_to)

    # Ambulance route: shortest path bottom-left -> right-top.
    amb_from, amb_to = _amb_endpoints(net, src_by_side, sink_by_side)
    if amb_from and amb_to:
        route_edges = _shortest_edges(net, amb_from, amb_to)
        if route_edges:
            ET.SubElement(root, "route", id="amb_route", edges=" ".join(route_edges))

    _sort_routes_by_begin(root)
    _write_xml(root, os.path.join(out, "routes.rou.xml"))


def _sort_routes_by_begin(root: ET.Element) -> None:
    """SUMO requires route input sorted by departure time, or it drops flows.

    Keep vType/route definitions first (in original order), then emit all
    time-bearing elements (flow/vehicle/trip) sorted by their begin time.
    """
    static_tags = {"vType", "route"}
    static = [c for c in list(root) if c.tag in static_tags]
    timed = [c for c in list(root) if c.tag not in static_tags]

    def begin_of(el: ET.Element) -> float:
        for attr in ("begin", "depart"):
            v = el.get(attr)
            if v is not None:
                try:
                    return float(v)
                except ValueError:
                    return 0.0
        return 0.0

    timed.sort(key=begin_of)
    for c in list(root):
        root.remove(c)
    for c in static:
        root.append(c)
    for c in timed:
        root.append(c)


def _flow(root, fid, src, dst, begin, end, veh_per_h, vtype="car"):
    if not src or not dst or veh_per_h <= 0:
        return
    prob = veh_per_h / 3600.0
    f = ET.SubElement(root, "flow", id=fid, type=vtype,
                      begin=str(int(begin)), end=str(int(end)),
                      probability=f"{prob:.5f}",
                      departLane="best", departSpeed="max")
    f.set("from", src)
    f.set("to", dst)


def _aligned_sink(net, src_edge, candidate_sinks):
    """Pick the sink best aligned opposite to the source (straight across)."""
    if not candidate_sinks:
        return None
    se = net.getEdge(src_edge)
    sx, sy = se.getFromNode().getCoord()
    best, bestd = None, 1e18
    for cand in candidate_sinks:
        ce = net.getEdge(cand)
        cx, cy = ce.getToNode().getCoord()
        # Straight-across: match the perpendicular coordinate.
        d = (sx - cx) ** 2 + (sy - cy) ** 2
        # Prefer sinks on the opposite side (far away) but aligned on the
        # shared axis: use alignment along one coordinate.
        align = min(abs(sx - cx), abs(sy - cy))
        score = align * 1000 - d * 0.0  # prioritise alignment
        if score < bestd:
            bestd = score
            best = cand
    return best


def _nearest_sink(net, src_edge, candidate_sinks):
    if not candidate_sinks:
        return None
    se = net.getEdge(src_edge)
    sx, sy = se.getFromNode().getCoord()
    best, bestd = None, 1e18
    for cand in candidate_sinks:
        ce = net.getEdge(cand)
        cx, cy = ce.getToNode().getCoord()
        d = (sx - cx) ** 2 + (sy - cy) ** 2
        if d < bestd:
            bestd = d
            best = cand
    return best


def _row_edges(net, y: float):
    """First source edge and last sink edge along a given row (constant y)."""
    src = sink = None
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        fx, fy = e.getFromNode().getCoord()
        tx, ty = e.getToNode().getCoord()
        if abs(fy - y) < 1 and abs(ty - y) < 1:
            fn = e.getFromNode()
            tn = e.getToNode()
            nbrs_f = _nbr_count(fn)
            nbrs_t = _nbr_count(tn)
            if nbrs_f == 1 and tx > fx:   # starts at west fringe, goes east
                src = e.getID()
            if nbrs_t == 1 and tx > fx:   # ends at east fringe, going east
                sink = e.getID()
    return src, sink


def _nbr_count(node) -> int:
    nbrs = set()
    for e in node.getIncoming() + node.getOutgoing():
        nbrs.add(e.getFromNode().getID())
        nbrs.add(e.getToNode().getID())
    nbrs.discard(node.getID())
    return len(nbrs)


def _amb_endpoints(net, src_by_side, sink_by_side):
    """Bottom-left source and right-top sink for the ambulance."""
    # bottom-left: a source with smallest x among S sources, or smallest y
    # among W sources. Choose the corner-most.
    amb_from = None
    best = 1e18
    for e in src_by_side["S"] + src_by_side["W"]:
        edge = net.getEdge(e)
        x, y = edge.getFromNode().getCoord()
        d = x + y
        if d < best:
            best = d
            amb_from = e
    amb_to = None
    best = -1e18
    for e in sink_by_side["E"] + sink_by_side["N"]:
        edge = net.getEdge(e)
        x, y = edge.getToNode().getCoord()
        d = x + y
        if d > best:
            best = d
            amb_to = e
    return amb_from, amb_to


def _shortest_edges(net, from_edge: str, to_edge: str) -> list[str]:
    fe = net.getEdge(from_edge)
    te = net.getEdge(to_edge)
    path, _cost = net.getShortestPath(fe, te)
    if path is None:
        return []
    return [e.getID() for e in path]


# --------------------------------------------------------------------------
# sumocfg writer
# --------------------------------------------------------------------------

def _write_sumocfg(path: str, net_file: str, route_files: list[str],
                   add_files: list[str], end: int) -> None:
    root = ET.Element("configuration")
    inp = ET.SubElement(root, "input")
    ET.SubElement(inp, "net-file", value=net_file)
    ET.SubElement(inp, "route-files", value=",".join(route_files))
    if add_files:
        ET.SubElement(inp, "additional-files", value=",".join(add_files))
    tm = ET.SubElement(root, "time")
    ET.SubElement(tm, "begin", value="0")
    ET.SubElement(tm, "end", value=str(end))
    ET.SubElement(tm, "step-length", value="1")
    proc = ET.SubElement(root, "processing")
    ET.SubElement(proc, "time-to-teleport", value="300")
    rep = ET.SubElement(root, "report")
    ET.SubElement(rep, "no-step-log", value="true")
    _write_xml(root, path)


# --------------------------------------------------------------------------
# Web scenario description
# --------------------------------------------------------------------------

def _write_web_scenario(sid: str, net_path: str, ctrl: list[str],
                        meta: dict) -> None:
    net = read_net(net_path)
    xmin, ymin, xmax, ymax = net.getBoundary()

    lanes = []
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        for lane in e.getLanes():
            shape = lane.getShape()
            lanes.append({
                "id": lane.getID(),
                "shape": [[round(x, 1), round(y, 1)] for x, y in shape],
                "width": round(lane.getWidth(), 2),
            })

    junctions = []
    for n in net.getNodes():
        shp = n.getShape()
        if shp and len(shp) >= 3:
            junctions.append({
                "id": n.getID(),
                "poly": [[round(x, 1), round(y, 1)] for x, y in shp],
            })

    lights = []
    for tid in ctrl:
        x, y = tls_coord(net, tid)
        lights.append({"id": tid, "x": round(x, 1), "y": round(y, 1)})

    doc = {
        "id": sid,
        "bounds": [round(xmin, 1), round(ymin, 1), round(xmax, 1), round(ymax, 1)],
        "lanes": lanes,
        "junctions": junctions,
        "lights": lights,
        "labels": meta.get("landmarks", []),
        "landmarks": meta.get("landmarks", []),
        "meta": {
            "title": meta["title"],
            "rotate_deg": meta["rotate_deg"],
            "rush": meta["rush"],
            "end": meta["end"],
        },
    }
    os.makedirs(WEB_SCEN_DIR, exist_ok=True)
    with open(os.path.join(WEB_SCEN_DIR, f"{sid}.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))


# --------------------------------------------------------------------------
# MIDTOWN scenario (OpenStreetMap)
# --------------------------------------------------------------------------

MIDTOWN_BBOX = (40.7515, -73.9870, 40.7665, -73.9680)   # S, W, N, E
OSM_SPEED_MS = 11.18          # 25 mph
GROW = 1.04                   # rotated rectangle grown 4%

# Corner intersections (avenue-keyword, street-keyword).
MIDTOWN_CORNERS = [
    ("6th", "47th"),
    ("6th", "55th"),
    ("3rd", "55th"),
    ("3rd", "47th"),
]


def _sumo_home() -> str:
    return os.path.dirname(os.path.dirname(sumolib.checkBinary("sumo")))


def _overpass_download(osm_path: str) -> None:
    """Download roads, buildings and parks for the Midtown bbox.

    Overpass's nginx front-end rejects some User-Agent strings with 406, so a
    clean token-style UA is used.
    """
    import urllib.request
    import urllib.parse

    if os.path.exists(osm_path) and os.path.getsize(osm_path) > 100_000:
        print(f"[midtown] OSM already present: {osm_path}")
        return

    s, w, n, e = MIDTOWN_BBOX
    bbox = f"{s},{w},{n},{e}"
    query = (
        "[out:xml][timeout:90];"
        "("
        f'way["highway"]({bbox});'
        f'way["building"]({bbox});'
        f'way["leisure"="park"]({bbox});'
        f'way["leisure"="garden"]({bbox});'
        ");(._;>;);out body;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=data,
        headers={"User-Agent": "CivicSignal-traffic-research/1.0"},
    )
    print("[midtown] downloading OSM from Overpass...")
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    with open(osm_path, "wb") as f:
        f.write(body)
    print(f"[midtown] wrote {osm_path} ({len(body)} bytes)")


def _netconvert_midtown(osm_path: str, net_path: str) -> None:
    keep_types = ",".join(
        f"highway.{t}" for t in (
            "primary", "primary_link", "secondary", "secondary_link",
            "tertiary", "tertiary_link", "residential", "unclassified",
            "unclassified_link",
        )
    )
    _run([
        _bin("netconvert"),
        "--osm-files", osm_path,
        "--output-file", net_path,
        "--keep-edges.by-type", keep_types,
        "--keep-edges.by-vclass", "passenger",
        "--geometry.remove", "--roundabouts.guess", "--ramps.guess",
        "--junctions.join",
        "--tls.guess-signals", "--tls.discard-simple", "--tls.join",
        "--tls.default-type", "static",
        "--output.street-names", "--no-turnarounds", "--edges.join",
        "--osm.sidewalks", "false", "--crossings.guess", "false",
    ])


def _node_streets(net: sumolib.net.Net):
    from collections import defaultdict
    ns = defaultdict(set)
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        nm = (e.getName() or "").lower()
        ns[e.getFromNode().getID()].add(nm)
        ns[e.getToNode().getID()].add(nm)
    return ns


def _find_corner(net, ns, av_key: str, st_key: str):
    for nid, streets in ns.items():
        has_av = any(av_key in s and ("ave" in s or "avenue" in s) for s in streets)
        has_st = any(st_key in s and ("st" in s or "street" in s) for s in streets)
        if has_av and has_st:
            return net.getNode(nid).getCoord()
    # looser fallback
    for nid, streets in ns.items():
        if any(av_key in s for s in streets) and any(st_key in s for s in streets):
            return net.getNode(nid).getCoord()
    return None


def _grow_rect(corners: list[tuple[float, float]], factor: float):
    """Grow a quadrilateral about its centroid by `factor`."""
    cx = sum(p[0] for p in corners) / len(corners)
    cy = sum(p[1] for p in corners) / len(corners)
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in corners]


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _rotation_angle(p_bottom: tuple[float, float], p_top: tuple[float, float]) -> float:
    """Angle (deg, clockwise from north, y-up) of the avenue going bottom->top."""
    dx = p_top[0] - p_bottom[0]
    dy = p_top[1] - p_bottom[1]
    return math.degrees(math.atan2(dx, dy))


def build_midtown() -> None:
    sid = "midtown"
    out = os.path.join(SCEN_DIR, sid)
    os.makedirs(out, exist_ok=True)
    osm_path = os.path.join(out, "midtown.osm.xml")
    raw_net = os.path.join(out, "net.raw.xml")
    net_path = os.path.join(out, "net.net.xml")

    _overpass_download(osm_path)
    print("[midtown] netconvert (full bbox)...")
    _netconvert_midtown(osm_path, raw_net)

    net = read_net(raw_net)
    ns = _node_streets(net)

    corners = []
    for av, st in MIDTOWN_CORNERS:
        c = _find_corner(net, ns, av, st)
        if c is None:
            raise RuntimeError(f"corner not found: {av} & {st}")
        corners.append(c)
        print(f"[midtown] corner {av} & {st} -> ({c[0]:.1f},{c[1]:.1f})")

    # Rotation: 6th Ave from 47th (corners[0]) to 55th (corners[1]).
    rotate_deg = _rotation_angle(corners[0], corners[1])
    print(f"[midtown] rotate_deg (6th Ave from north) = {rotate_deg:.1f}")

    poly = _grow_rect(corners, GROW)

    # Keep edges with at least one end inside the grown rectangle.
    keep_ids = []
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        fx, fy = e.getFromNode().getCoord()
        tx, ty = e.getToNode().getCoord()
        if _point_in_poly(fx, fy, poly) or _point_in_poly(tx, ty, poly):
            keep_ids.append(e.getID())
    keep_ids = sorted(set(keep_ids))
    print(f"[midtown] keeping {len(keep_ids)} edges inside rectangle")

    # Keep-list file.
    keep_file = os.path.join(out, "keep_edges.txt")
    with open(keep_file, "w") as f:
        f.write("\n".join(keep_ids) + "\n")

    # Edge-diff file: set every kept edge's speed to 25 mph.
    ediff = ET.Element("edges")
    for eid in keep_ids:
        ET.SubElement(ediff, "edge", id=eid, speed=f"{OSM_SPEED_MS}")
    edge_diff = os.path.join(out, "speed.edg.xml")
    _write_xml(ediff, edge_diff)

    # Second netconvert pass: prune to rectangle + apply speed diff.
    print("[midtown] netconvert (prune to rectangle + speed diff)...")
    _run([
        _bin("netconvert"),
        "--sumo-net-file", raw_net,
        "--edge-files", edge_diff,
        "--keep-edges.input-file", keep_file,
        "--output-file", net_path,
        "--tls.discard-simple", "--tls.join",
    ])

    ctrl = controllable_tls(net_path)
    print(f"[midtown] controllable lights: {len(ctrl)}")

    net2 = read_net(net_path)

    # Build demand, buses, ambulance, events.
    meta = _build_midtown_routes(out, net2, corners, rotate_deg, ctrl)

    # TLS programs (avenues are the major axis).
    av_dir = _unit(corners[0], corners[1])    # along 6th Ave (an avenue)
    geoms: dict[str, TLSGeom] = {}
    for tid in ctrl:
        x, y = tls_coord(net2, tid)
        geoms[tid] = TLSGeom(tid, x, y, major_dir=av_dir)
    tls_add = os.path.join(out, "tls.add.xml")
    build_tls_add(net_path, tls_add, geoms, major_speed=OSM_SPEED_MS)
    print(f"[midtown] wrote {tls_add}")

    # Map extras: building/park polygons.
    poly_add = _build_midtown_polys(out, osm_path, net_path)

    # sumocfg
    add_files = ["tls.add.xml"]
    if poly_add:
        add_files.append(os.path.basename(poly_add))
    cfg_path = os.path.join(out, "sim.sumocfg")
    _write_sumocfg(cfg_path, "net.net.xml", ["routes.rou.xml"], add_files, end=2400)

    meta["cfg"] = os.path.relpath(cfg_path, ROOT)
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("[midtown] wrote meta.json")

    _write_web_scenario_midtown(sid, net_path, ctrl, meta, poly_add)
    print(f"[midtown] wrote web/scenarios/{sid}.json")

    print(f"[midtown] OK: {len(ctrl)} controllable lights (expected 54)")
    if len(ctrl) != 54:
        print(f"[midtown] WARNING: expected 54 signals, got {len(ctrl)}")


def _unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    return (dx / d, dy / d)


def _street_label(name: str) -> str:
    """Shorten 'West 52nd Street' -> 'W 52nd St', '5th Avenue' -> '5th Ave'."""
    if not name:
        return ""
    s = name
    s = s.replace("Avenue", "Ave").replace("Street", "St")
    s = s.replace("West ", "W ").replace("East ", "E ")
    return s.strip()


def _build_midtown_routes(out, net, corners, rotate_deg, ctrl) -> dict:
    """Per-lane probability demand on avenues/streets, buses, ambulance,
    surge and crash specs. Returns the meta dict (minus cfg)."""
    import random
    rng = random.Random(7)

    root = ET.Element("routes")
    for child in ET.fromstring("<routes>" + VTYPES + "</routes>"):
        root.append(child)

    # Classify fringe edges (dead-end sources/sinks) and tag by street name.
    sources = fringe_source_edges(net)
    sinks = fringe_sink_edges(net)

    def name_of(eid: str) -> str:
        return (net.getEdge(eid).getName() or "").lower()

    def is_avenue(nm: str) -> bool:
        return "ave" in nm

    def lanes_of(eid: str) -> int:
        return min(4, net.getEdge(eid).getLaneNumber())

    def far_exit_same_name(src: str, pool: list[str]) -> Optional[str]:
        nm = name_of(src)
        se = net.getEdge(src)
        sx, sy = se.getFromNode().getCoord()
        best, bestd = None, -1.0
        for cand in pool:
            if name_of(cand) != nm:
                continue
            ce = net.getEdge(cand)
            cx, cy = ce.getToNode().getCoord()
            d = (sx - cx) ** 2 + (sy - cy) ** 2
            if d > bestd:
                bestd, best = d, cand
        return best

    periods = [(0, 600, False), (600, 1800, True), (1800, 2400, False)]
    fid = 0
    for src in sources:
        nm = name_of(src)
        lanes = lanes_of(src)
        far = far_exit_same_name(src, sinks)
        others = [s for s in sinks if s != src and s != far]
        rng.shuffle(others)
        rand3 = others[:3]
        for (b, e, rush) in periods:
            if is_avenue(nm):
                per_lane = 230 if rush else 90
            else:
                per_lane = 120 if rush else 70
            vph = per_lane * lanes
            # 70% to farthest same-name exit, rest split over 3 random exits.
            if far:
                _flow(root, f"m{fid}", src, far, b, e, vph * 0.7); fid += 1
            share = (vph * 0.3) / max(1, len(rand3))
            for dst in rand3:
                _flow(root, f"m{fid}", src, dst, b, e, share); fid += 1

    # Buses: southbound 5th Ave, northbound Madison, every 150 s.
    _add_midtown_bus(root, net, sources, sinks, "5th", southbound=True)
    _add_midtown_bus(root, net, sources, sinks, "madison", southbound=False)

    # Ambulance route: south end of 3rd Ave -> West 55th St exit.
    amb_from = _end_of_avenue(net, sources, "3rd", south=True)
    amb_to = _street_exit(net, sinks, "55th", west=True)
    amb_label = "Ambulance \u2192 Mount Sinai West"
    if amb_from and amb_to:
        edges = _shortest_edges(net, amb_from, amb_to)
        if edges:
            ET.SubElement(root, "route", id="amb_route", edges=" ".join(edges))

    _sort_routes_by_begin(root)
    _write_xml(root, os.path.join(out, "routes.rou.xml"))

    # Surge: "Radio City show lets out" - 160 cars from edges at 6th & 50th
    # to the 6 farthest exits, over 240 s.
    surge_from = _edges_at(net, sources, "6th", "50th")
    surge_to = _farthest_exits(net, surge_from or sources, sinks, 6)

    # Crash: 5th Ave 51st -> 50th, lanes 0,1, 300 s.
    crash_edge = _edge_between(net, "5th", "51st", "50th") or _any_named(net, "5th")

    meta = {
        "id": "midtown",
        "title": "Midtown Manhattan",
        "rush": [600, 1800],
        "end": 2400,
        "expected_tls": 54,
        "rotate_deg": round(rotate_deg, 1),
        "attribution": "\u00a9 OpenStreetMap contributors",
        "ambulance": {"label": amb_label, "route": "amb_route"},
        "surge": {
            "label": "Radio City show lets out",
            "from": surge_from,
            "to": surge_to,
            "n": 160,
            "over": 240,
        },
        "crash": {
            "label": "Crash on 5th Ave at St. Patrick's",
            "edge": crash_edge,
            "lanes": [0, 1],
            "pos": 20.0,
            "duration": 300,
        },
        "landmarks": _midtown_landmarks(net, corners),
    }
    return meta


def _add_midtown_bus(root, net, sources, sinks, key, southbound):
    """Add a bus flow along a named avenue in one direction, every 150 s."""
    # Source whose avenue matches and whose travel direction matches.
    cand_src = None
    for s in sources:
        if key in (net.getEdge(s).getName() or "").lower():
            fx, fy = net.getEdge(s).getFromNode().getCoord()
            tx, ty = net.getEdge(s).getToNode().getCoord()
            going_south = ty < fy
            if going_south == southbound:
                cand_src = s
                break
    if not cand_src:
        return
    dst = _end_of_same(net, cand_src, sinks)
    if not dst:
        return
    f = ET.SubElement(root, "flow", id=f"bus_{key}", type="bus",
                      begin="0", end="2400", period="150",
                      departLane="best", departSpeed="max")
    f.set("from", cand_src)
    f.set("to", dst)


def _end_of_same(net, src, sinks):
    nm = (net.getEdge(src).getName() or "").lower()
    se = net.getEdge(src)
    sx, sy = se.getFromNode().getCoord()
    best, bestd = None, -1.0
    for c in sinks:
        if (net.getEdge(c).getName() or "").lower() != nm:
            continue
        cx, cy = net.getEdge(c).getToNode().getCoord()
        d = (sx - cx) ** 2 + (sy - cy) ** 2
        if d > bestd:
            bestd, best = d, c
    return best


def _end_of_avenue(net, sources, key, south):
    best, bestv = None, None
    for s in sources:
        if key not in (net.getEdge(s).getName() or "").lower():
            continue
        x, y = net.getEdge(s).getFromNode().getCoord()
        v = y if south else -y
        if bestv is None or v < bestv:
            bestv, best = v, s
    return best


def _street_exit(net, sinks, key, west):
    best, bestv = None, None
    for s in sinks:
        if key not in (net.getEdge(s).getName() or "").lower():
            continue
        x, y = net.getEdge(s).getToNode().getCoord()
        v = x if west else -x
        if bestv is None or v < bestv:
            bestv, best = v, s
    return best


def _edges_at(net, sources, av_key, st_key):
    """Source edges whose node touches both an avenue and a street name."""
    out = []
    for s in sources:
        e = net.getEdge(s)
        for node in (e.getFromNode(), e.getToNode()):
            names = {(ie.getName() or "").lower()
                     for ie in node.getIncoming() + node.getOutgoing()}
            if any(av_key in n for n in names) and any(st_key in n for n in names):
                out.append(s)
                break
    return out


def _farthest_exits(net, from_edges, sinks, k):
    if not from_edges:
        return sinks[:k]
    fe = net.getEdge(from_edges[0])
    fx, fy = fe.getFromNode().getCoord()
    scored = []
    for s in sinks:
        cx, cy = net.getEdge(s).getToNode().getCoord()
        scored.append(((fx - cx) ** 2 + (fy - cy) ** 2, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:k]]


def _edge_between(net, name_key, from_st, to_st):
    """A directed edge on a named avenue between two cross streets."""
    cands = [e for e in net.getEdges()
             if not e.getID().startswith(":")
             and name_key in (e.getName() or "").lower()]
    for e in cands:
        fn = {(ie.getName() or "").lower()
              for ie in e.getFromNode().getIncoming() + e.getFromNode().getOutgoing()}
        tn = {(ie.getName() or "").lower()
              for ie in e.getToNode().getIncoming() + e.getToNode().getOutgoing()}
        if any(from_st in n for n in fn) and any(to_st in n for n in tn):
            return e.getID()
    return None


def _any_named(net, name_key):
    for e in net.getEdges():
        if not e.getID().startswith(":") and name_key in (e.getName() or "").lower():
            return e.getID()
    return None


def _midtown_landmarks(net, corners):
    cx = sum(p[0] for p in corners) / 4
    cy = sum(p[1] for p in corners) / 4
    return [
        {"name": "Radio City", "x": round(cx - 250, 1), "y": round(cy + 120, 1)},
        {"name": "St. Patrick's", "x": round(cx, 1), "y": round(cy - 40, 1)},
        {"name": "MoMA", "x": round(cx - 100, 1), "y": round(cy + 260, 1)},
        {"name": "Grand Central \u2193", "x": round(cx + 150, 1), "y": round(cy - 320, 1)},
    ]


def _build_midtown_polys(out, osm_path, net_path):
    """Building/park outlines via polyconvert with SUMO's osmPolyconvert.typ.xml."""
    typ = os.path.join(_sumo_home(), "data", "typemap", "osmPolyconvert.typ.xml")
    if not os.path.exists(typ):
        print("[midtown] polyconvert typemap missing; skipping polygons")
        return None
    poly_path = os.path.join(out, "shapes.poly.xml")
    try:
        _run([
            _bin("polyconvert"),
            "--osm-files", osm_path,
            "--net-file", net_path,
            "--type-file", typ,
            "--output-file", poly_path,
        ])
    except subprocess.CalledProcessError:
        print("[midtown] polyconvert failed; skipping polygons")
        return None
    return poly_path


def _write_web_scenario_midtown(sid, net_path, ctrl, meta, poly_add):
    net = read_net(net_path)
    xmin, ymin, xmax, ymax = net.getBoundary()

    lanes = []
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        for lane in e.getLanes():
            lanes.append({
                "id": lane.getID(),
                "shape": [[round(x, 1), round(y, 1)] for x, y in lane.getShape()],
                "width": round(lane.getWidth(), 2),
            })

    junctions = []
    for n in net.getNodes():
        shp = n.getShape()
        if shp and len(shp) >= 3:
            junctions.append({
                "id": n.getID(),
                "poly": [[round(x, 1), round(y, 1)] for x, y in shp],
            })

    lights = []
    for tid in ctrl:
        x, y = tls_coord(net, tid)
        lights.append({"id": tid, "x": round(x, 1), "y": round(y, 1)})

    # Street labels: one per street name, mid of its longest edge.
    labels = _street_labels(net)

    # Building/park polygons for the offscreen base layer.
    buildings = []
    if poly_add and os.path.exists(poly_add):
        pt = ET.parse(poly_add)
        for p in pt.getroot().findall("poly"):
            shape = p.get("shape", "")
            pts = []
            for pair in shape.split():
                xs, ys = pair.split(",")
                pts.append([round(float(xs), 1), round(float(ys), 1)])
            if len(pts) >= 3:
                typ = p.get("type", "")
                kind = "park" if ("park" in typ or "garden" in typ) else "building"
                buildings.append({"poly": pts, "kind": kind})

    doc = {
        "id": sid,
        "bounds": [round(xmin, 1), round(ymin, 1), round(xmax, 1), round(ymax, 1)],
        "lanes": lanes,
        "junctions": junctions,
        "lights": lights,
        "buildings": buildings,
        "labels": labels,
        "landmarks": meta.get("landmarks", []),
        "meta": {
            "title": meta["title"],
            "rotate_deg": meta["rotate_deg"],
            "rush": meta["rush"],
            "end": meta["end"],
            "attribution": meta.get("attribution", ""),
        },
    }
    os.makedirs(WEB_SCEN_DIR, exist_ok=True)
    with open(os.path.join(WEB_SCEN_DIR, f"{sid}.json"), "w") as f:
        json.dump(doc, f, separators=(",", ":"))


def _street_labels(net):
    """One label per street name, placed at the middle of its longest edge."""
    from collections import defaultdict
    by_name = defaultdict(list)
    for e in net.getEdges():
        if e.getID().startswith(":"):
            continue
        nm = e.getName()
        if nm:
            by_name[nm].append(e)
    labels = []
    for nm, edges in by_name.items():
        longest = max(edges, key=lambda e: e.getLength())
        shape = longest.getShape()
        mid = shape[len(shape) // 2]
        labels.append({
            "name": _street_label(nm),
            "x": round(mid[0], 1),
            "y": round(mid[1], 1),
        })
    return labels


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    which = argv[1] if len(argv) > 1 else "grid"
    if which == "grid":
        build_grid()
    elif which == "midtown":
        build_midtown()
    else:
        print(f"unknown scenario: {which}")
        return 2
    return 0


if __name__ == "__main__":
    # Allow running as a script: python scenario/build.py grid
    sys.path.insert(0, ROOT)
    raise SystemExit(main(sys.argv))
