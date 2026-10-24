from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Weights:
    flow: float = 1.0
    climate: float = 0.0
    transit: float = 0.0
    fairness: float = 0.0
    preempt: bool = True


@dataclass
class TLSInfo:
    tls_id: str
    links: list[tuple[str, str]]          # links[i] = (in_lane, out_lane)
    green_states: list[str]
    initial_state: str


@dataclass
class LaneObs:
    halting: int = 0
    co2_mg_s: float = 0.0
    buses: int = 0
    max_wait_s: float = 0.0
    emergency_dist_m: Optional[float] = None
    approaching: int = 0
    buses_moving: int = 0
