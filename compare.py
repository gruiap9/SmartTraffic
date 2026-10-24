from __future__ import annotations

"""CivicSignal benchmark.

Runs every strategy on every map and seed in parallel, parses tripinfo, and
writes mean +/- std comparison tables (markdown + CSV) to results/.

Usage:
    python compare.py --scenario grid|midtown|all --seeds 1 2 3
    python compare.py --scenario all --quick        # 1 seed, 900 s
"""

import argparse
import csv
import math
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.controller import WeightedMaxPressure, BALANCED  # noqa: E402

RESULTS = os.path.join(ROOT, "results")

STRATEGIES = ["fixed", "actuated", "greenwave", "adaptive"]
STRATEGY_LABEL = {
    "fixed": "fixed",
    "actuated": "actuated",
    "greenwave": "green wave",
    "adaptive": "CivicSignal",
}
BASELINES = ["fixed", "actuated", "greenwave"]

# Ambulance dispatch times.
AMB_TIMES = [700, 900, 1100, 1300, 1500]
AMB_TIMES_QUICK = [550, 700]

# Fuel cost.
GAL_PER_LITRE = 1.0 / 3.785411784
USD_PER_GAL = 3.50

SCEN_CFG = {
    "grid": os.path.join(ROOT, "scenario", "grid", "sim.sumocfg"),
    "midtown": os.path.join(ROOT, "scenario", "midtown", "sim.sumocfg"),
}


# ----------------------------------------------------------------------
# Metrics parsed from one run.
# ----------------------------------------------------------------------

@dataclass
class RunResult:
    scenario: str
    strategy: str
    seed: int
    trips: int = 0
    delay_s: float = 0.0          # mean waitingTime (excl. ambulances)
    time_loss_s: float = 0.0      # mean timeLoss
    stops: float = 0.0            # mean waitingCount
    co2_kg: float = 0.0           # total CO2
    idle_co2_kg: float = 0.0      # from runner (idling CO2)
    fuel_l: float = 0.0           # total fuel litres
    fuel_usd: float = 0.0
    nox_g: float = 0.0
    pm_g: float = 0.0
    bus_trip_s: float = 0.0       # mean bus trip
    amb_trip_s: float = 0.0       # mean ambulance trip
    teleports: int = 0


# ----------------------------------------------------------------------
# Single run (executed in a worker process).
# ----------------------------------------------------------------------

def run_one(scenario: str, strategy: str, seed: int, end: int,
            amb_times: list[int]) -> RunResult:
    # Import inside the worker so each process has a clean traci state.
    from core.sumo_runner import SumoRunner
    from core.controller import WeightedMaxPressure, BALANCED
    import json

    cfg = SCEN_CFG[scenario]
    meta = json.load(open(os.path.join(os.path.dirname(cfg), "meta.json")))
    amb_route = meta.get("ambulance", {}).get("route", "amb_route")

    tripinfo = os.path.join(RESULTS, f"tripinfo_{scenario}_{strategy}_{seed}.xml")
    os.makedirs(RESULTS, exist_ok=True)

    controller = None
    program = "0"
    if strategy == "adaptive":
        controller = WeightedMaxPressure(BALANCED)
    elif strategy == "fixed":
        program = "0"
    else:
        program = strategy   # "actuated" or "greenwave"

    label = f"{scenario}_{strategy}_{seed}"
    runner = SumoRunner(label, cfg, seed=seed, controller=controller,
                        tripinfo=tripinfo, program=program)

    amb_set = set(amb_times)
    amb_trips: list[float] = []
    prev_amb_last = 0.0
    for _ in range(end):
        if runner.t in amb_set:
            runner.spawn_ambulance(amb_route)
        m = runner.step()
        if m["m"]["amb_last_trip_s"] != prev_amb_last:
            prev_amb_last = m["m"]["amb_last_trip_s"]
            amb_trips.append(prev_amb_last)

    final = m["m"]
    runner.close()

    res = _parse_tripinfo(scenario, strategy, seed, tripinfo)
    # Fill runner-sourced metrics.
    res.idle_co2_kg = final["idle_co2_kg"]
    res.teleports = final["teleports"]
    res.bus_trip_s = final["bus_avg_trip_s"]
    res.amb_trip_s = statistics.mean(amb_trips) if amb_trips else 0.0
    return res


def _parse_tripinfo(scenario, strategy, seed, path) -> RunResult:
    res = RunResult(scenario=scenario, strategy=strategy, seed=seed)
    if not os.path.exists(path):
        return res
    tree = ET.parse(path)
    waits, losses, stops = [], [], []
    co2 = fuel = nox = pm = 0.0
    bus_durs = []
    for tp in tree.getroot().findall("tripinfo"):
        vtype = tp.get("vType", "")
        if vtype == "ambulance":
            continue  # excluded from headline metrics
        waits.append(float(tp.get("waitingTime", 0)))
        losses.append(float(tp.get("timeLoss", 0)))
        stops.append(float(tp.get("waitingCount", 0)))
        if vtype == "bus":
            bus_durs.append(float(tp.get("duration", 0)))
        em = tp.find("emissions")
        if em is not None:
            co2 += float(em.get("CO2_abs", 0))
            fuel += float(em.get("fuel_abs", 0))
            nox += float(em.get("NOx_abs", 0))
            pm += float(em.get("PMx_abs", 0))
    res.trips = len(waits)
    res.delay_s = statistics.mean(waits) if waits else 0.0
    res.time_loss_s = statistics.mean(losses) if losses else 0.0
    res.stops = statistics.mean(stops) if stops else 0.0
    res.co2_kg = co2 / 1e6
    # SUMO fuel_abs is in mg; litres via 745 g/l (matches the runner).
    res.fuel_l = fuel / 745000.0
    res.fuel_usd = res.fuel_l * GAL_PER_LITRE * USD_PER_GAL
    res.nox_g = nox / 1000.0
    res.pm_g = pm / 1000.0
    if bus_durs:
        res.bus_trip_s = statistics.mean(bus_durs)
    return res


# ----------------------------------------------------------------------
# Aggregation / reporting.
# ----------------------------------------------------------------------

# (label, attr, lower_is_better, formatter)
METRICS = [
    ("Delay (s)", "delay_s", True, "{:.1f}"),
    ("Time loss (s)", "time_loss_s", True, "{:.1f}"),
    ("Stops", "stops", True, "{:.2f}"),
    ("CO2 (kg)", "co2_kg", True, "{:.1f}"),
    ("Idle CO2 (kg)", "idle_co2_kg", True, "{:.1f}"),
    ("Fuel ($)", "fuel_usd", True, "{:.2f}"),
    ("NOx (g)", "nox_g", True, "{:.1f}"),
    ("PM (g)", "pm_g", True, "{:.2f}"),
    ("Bus trip (s)", "bus_trip_s", True, "{:.1f}"),
    ("Amb trip (s)", "amb_trip_s", True, "{:.1f}"),
    ("Trips", "trips", False, "{:.0f}"),
    ("Teleports", "teleports", True, "{:.0f}"),
]


def _agg(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.mean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, std


def write_reports(scenario: str, results: list[RunResult]) -> str:
    by_strat: dict[str, list[RunResult]] = {s: [] for s in STRATEGIES}
    for r in results:
        by_strat[r.strategy].append(r)

    # Aggregate per strategy per metric.
    agg: dict[str, dict[str, tuple[float, float]]] = {}
    for s in STRATEGIES:
        agg[s] = {}
        for (_lbl, attr, _low, _fmt) in METRICS:
            agg[s][attr] = _agg([getattr(r, attr) for r in by_strat[s]])

    md = _markdown_table(scenario, agg, by_strat)
    md_path = os.path.join(RESULTS, f"compare_{scenario}.md")
    with open(md_path, "w") as f:
        f.write(md)

    _write_csv(scenario, agg, results)
    return md


def _best_strategy(agg, attr, lower_is_better) -> str:
    vals = {s: agg[s][attr][0] for s in STRATEGIES}
    if lower_is_better:
        return min(vals, key=vals.get)
    return max(vals, key=vals.get)


def _best_baseline(agg, attr, lower_is_better) -> tuple[str, float]:
    vals = {s: agg[s][attr][0] for s in BASELINES}
    if lower_is_better:
        s = min(vals, key=vals.get)
    else:
        s = max(vals, key=vals.get)
    return s, vals[s]


def _markdown_table(scenario, agg, by_strat) -> str:
    lines = [f"# CivicSignal benchmark — {scenario}", ""]
    nseeds = max((len(v) for v in by_strat.values()), default=0)
    lines.append(f"Seeds per strategy: {nseeds}. "
                 f"Best value in **bold**. "
                 f"'vs best baseline' compares CivicSignal (adaptive) to the "
                 f"best of fixed/actuated/green wave.")
    lines.append("")

    # Header.
    header = ["Metric"] + [STRATEGY_LABEL[s] for s in STRATEGIES] + \
             ["CivicSignal vs best baseline"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))

    wins = 0
    comparable = 0
    for (lbl, attr, low, fmt) in METRICS:
        best = _best_strategy(agg, attr, low)
        row = [lbl]
        for s in STRATEGIES:
            mean, std = agg[s][attr]
            cell = f"{fmt.format(mean)} ± {fmt.format(std)}"
            if s == best:
                cell = f"**{cell}**"
            row.append(cell)
        # vs best baseline.
        bl_name, bl_val = _best_baseline(agg, attr, low)
        civ_val = agg["adaptive"][attr][0]
        delta = _pct_delta(civ_val, bl_val, low)
        row.append(delta)
        comparable += 1
        if best == "adaptive":
            wins += 1
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append(f"**Wins (CivicSignal best of all four): {wins}/{comparable} "
                 f"metrics.**")

    # vs-fixed summary line (delay + CO2).
    d_civ = agg["adaptive"]["delay_s"][0]
    d_fix = agg["fixed"]["delay_s"][0]
    c_civ = agg["adaptive"]["co2_kg"][0]
    c_fix = agg["fixed"]["co2_kg"][0]
    lines.append("")
    lines.append(
        f"**vs fixed:** delay {_signed_pct(d_civ, d_fix)}, "
        f"CO2 {_signed_pct(c_civ, c_fix)}."
    )

    # Every run listed.
    lines.append("")
    lines.append("## Every run")
    lines.append("")
    run_header = ["Strategy", "Seed", "Trips", "Delay", "TimeLoss", "Stops",
                  "CO2 kg", "Fuel $", "NOx g", "PM g", "Amb s", "Bus s", "Tele"]
    lines.append("| " + " | ".join(run_header) + " |")
    lines.append("|" + "---|" * len(run_header))
    runs = sorted(
        [r for v in by_strat.values() for r in v],
        key=lambda r: (STRATEGIES.index(r.strategy), r.seed),
    )
    for r in runs:
        lines.append("| " + " | ".join([
            STRATEGY_LABEL[r.strategy], str(r.seed), f"{r.trips}",
            f"{r.delay_s:.1f}", f"{r.time_loss_s:.1f}", f"{r.stops:.2f}",
            f"{r.co2_kg:.1f}", f"{r.fuel_usd:.2f}", f"{r.nox_g:.1f}",
            f"{r.pm_g:.2f}", f"{r.amb_trip_s:.1f}", f"{r.bus_trip_s:.1f}",
            f"{r.teleports}",
        ]) + " |")

    lines.append("")
    return "\n".join(lines)


def _pct_delta(civ: float, baseline: float, lower_is_better: bool) -> str:
    if baseline == 0:
        return "n/a"
    pct = (civ - baseline) / abs(baseline) * 100.0
    # For lower-is-better, a negative pct is an improvement.
    better = (pct < 0) if lower_is_better else (pct > 0)
    sign = "+" if pct >= 0 else ""
    tag = "better" if better else "worse"
    return f"{sign}{pct:.1f}% ({tag})"


def _signed_pct(civ: float, baseline: float) -> str:
    if baseline == 0:
        return "n/a"
    pct = (civ - baseline) / abs(baseline) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _write_csv(scenario, agg, results: list[RunResult]) -> None:
    csv_path = os.path.join(RESULTS, f"compare_{scenario}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "strategy", "seed", "trips", "delay_s",
                    "time_loss_s", "stops", "co2_kg", "idle_co2_kg", "fuel_l",
                    "fuel_usd", "nox_g", "pm_g", "bus_trip_s", "amb_trip_s",
                    "teleports"])
        for r in sorted(results, key=lambda r: (STRATEGIES.index(r.strategy), r.seed)):
            w.writerow([r.scenario, r.strategy, r.seed, r.trips,
                        f"{r.delay_s:.3f}", f"{r.time_loss_s:.3f}",
                        f"{r.stops:.3f}", f"{r.co2_kg:.4f}",
                        f"{r.idle_co2_kg:.4f}", f"{r.fuel_l:.4f}",
                        f"{r.fuel_usd:.4f}", f"{r.nox_g:.3f}", f"{r.pm_g:.4f}",
                        f"{r.bus_trip_s:.2f}", f"{r.amb_trip_s:.2f}",
                        r.teleports])
        # Aggregate rows.
        w.writerow([])
        w.writerow(["# aggregate mean +/- std"])
        w.writerow(["strategy"] + [m[0] for m in METRICS])
        for s in STRATEGIES:
            row = [STRATEGY_LABEL[s]]
            for (_lbl, attr, _low, fmt) in METRICS:
                mean, std = agg[s][attr]
                row.append(f"{fmt.format(mean)}±{fmt.format(std)}")
            w.writerow(row)


# ----------------------------------------------------------------------
# Driver.
# ----------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["grid", "midtown", "all"],
                    default="all")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--quick", action="store_true",
                    help="1 seed, 900 s")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    scenarios = ["grid", "midtown"] if args.scenario == "all" else [args.scenario]
    if args.quick:
        seeds = [args.seeds[0]]
        end = 900
        amb_times = AMB_TIMES_QUICK
    else:
        seeds = args.seeds
        end = 2400
        amb_times = AMB_TIMES

    jobs = [(sc, st, sd, end, amb_times)
            for sc in scenarios for st in STRATEGIES for sd in seeds]
    print(f"[compare] {len(jobs)} runs "
          f"({len(scenarios)} maps x {len(STRATEGIES)} strategies x "
          f"{len(seeds)} seeds), end={end}s, {args.workers} workers")

    results: list[RunResult] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, *j): j for j in jobs}
        for fut in as_completed(futs):
            j = futs[fut]
            try:
                r = fut.result()
                results.append(r)
                print(f"  done {r.scenario}/{r.strategy}/{r.seed}: "
                      f"delay={r.delay_s:.1f}s co2={r.co2_kg:.1f}kg "
                      f"trips={r.trips}")
            except Exception as e:
                print(f"  FAILED {j[0]}/{j[1]}/{j[2]}: {e}")

    for sc in scenarios:
        sc_results = [r for r in results if r.scenario == sc]
        if sc_results:
            md = write_reports(sc, sc_results)
            print(f"\n{'='*70}\n{md}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
