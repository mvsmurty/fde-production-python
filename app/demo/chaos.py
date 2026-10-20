"""Per-request failure injection for the classroom demo. Not part of the production design."""

import time
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

Scenario = Literal[
    "none", "slow", "load", "ratelimit", "flood", "hang", "outage", "bug", "garbage", "oom"
]
SCENARIOS: tuple[Scenario, ...] = (
    "none", "slow", "load", "ratelimit", "flood", "hang", "outage", "bug", "garbage", "oom"
)


@dataclass
class ChaosState:
    scenario: Scenario = "none"
    t0: float = field(default_factory=time.perf_counter)
    attempts: Counter[str] = field(default_factory=Counter)
    spans: list[dict[str, object]] = field(default_factory=list)


chaos_var: ContextVar[ChaosState | None] = ContextVar("chaos", default=None)


def current() -> ChaosState:
    return chaos_var.get() or ChaosState()
