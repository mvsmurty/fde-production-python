"""A stand-in for an LLM provider SDK.

`MockLLMClient.complete` blocks like `OpenAI().chat.completions.create`;
`MockLLMClient.acomplete` awaits like `AsyncOpenAI().chat.completions.create`.
Answers are keyword-based so the demo is deterministic and needs no API key.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Literal

from app.demo import chaos

Task = Literal["summarize", "classify", "sentiment"]

BASE_LATENCY_S = 0.4
SLOW_LATENCY_S = 1.2
HANG_S = 12.0


class LLMUnavailableError(Exception):
    """The provider returned 503 / overloaded."""


@dataclass
class _Plan:
    delay: float
    fail: bool = False
    garbage: bool = False


def _plan(task: Task) -> _Plan:
    state = chaos.current()
    state.attempts[task] += 1
    attempt = state.attempts[task]
    match state.scenario:
        case "slow":
            return _Plan(SLOW_LATENCY_S)
        case "hang" if task == "sentiment":
            return _Plan(HANG_S)
        case "flaky" if task == "classify" and attempt == 1:
            return _Plan(0.2, fail=True)
        case "garbage" if task == "classify":
            return _Plan(BASE_LATENCY_S, garbage=True)
        case _:
            return _Plan(BASE_LATENCY_S)


def _answer(task: Task, text: str, plan: _Plan) -> str:
    if plan.fail:
        raise LLMUnavailableError("503 Service Unavailable: model overloaded, retry later")
    lowered = text.lower()
    if task == "summarize":
        first = text.strip().split("\n")[0][:80]
        return f"Customer reports: {first}"
    if task == "classify":
        if plan.garbage:
            return json.dumps({"category": "Billing Issue??", "priority": "URGENT!!!"})
        if any(w in lowered for w in ("refund", "charged", "payment", "invoice")):
            return json.dumps({"category": "billing", "priority": "P2"})
        if any(w in lowered for w in ("down", "not working", "error", "crash")):
            return json.dumps({"category": "technical", "priority": "P1"})
        if any(w in lowered for w in ("password", "login", "account")):
            return json.dumps({"category": "account", "priority": "P2"})
        return json.dumps({"category": "general", "priority": "P3"})
    angry = ("frustrat", "angry", "worst", "asap", "terrible", "unacceptable")
    return "negative" if any(w in lowered for w in angry) else "neutral"


class _Span:
    def __init__(self, task: Task):
        self.state = chaos.current()
        self.task = task
        self.attempt = self.state.attempts[task]
        self.start = time.perf_counter() - self.state.t0

    def close(self, outcome: str) -> None:
        self.state.spans.append({
            "task": self.task,
            "attempt": self.attempt,
            "start_ms": round(self.start * 1000),
            "end_ms": round((time.perf_counter() - self.state.t0) * 1000),
            "outcome": outcome,
        })


class MockLLMClient:
    def complete(self, task: Task, text: str) -> str:
        plan = _plan(task)
        span = _Span(task)
        try:
            time.sleep(plan.delay)
            result = _answer(task, text, plan)
        except Exception:
            span.close("error")
            raise
        span.close("ok")
        return result

    async def acomplete(self, task: Task, text: str) -> str:
        plan = _plan(task)
        span = _Span(task)
        try:
            await asyncio.sleep(plan.delay)
            result = _answer(task, text, plan)
        except asyncio.CancelledError:
            span.close("timeout")
            raise
        except Exception:
            span.close("error")
            raise
        span.close("ok")
        return result


llm = MockLLMClient()
