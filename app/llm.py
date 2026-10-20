"""LLM clients: a deterministic mock and a real xAI (Grok) client, behind one `llm` object.

Each client has `complete` (blocks, like `OpenAI()` / `httpx.Client`) and
`acomplete` (awaits, like `AsyncOpenAI()` / `httpx.AsyncClient`).
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import get_settings
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


PROMPTS: dict[Task, str] = {
    "summarize": (
        "Summarise this customer support ticket in one sentence of at most 25 words. "
        "Reply with the sentence only."
    ),
    "classify": (
        'Classify this customer support ticket. Reply with only a JSON object: '
        '{"category": "...", "priority": "..."}. '
        "category is one of: billing, technical, account, general. "
        "priority is P1 (service down or money lost), P2 (customer blocked) or P3 (question)."
    ),
    "sentiment": (
        "What is the customer's sentiment in this support ticket? "
        "Reply with exactly one word: positive, neutral or negative."
    ),
}


class XAIClient:
    """Calls xAI's OpenAI-compatible chat completions API."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._async: httpx.AsyncClient | None = None

    def _payload(self, task: Task, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": PROMPTS[task]},
                {"role": "user", "content": text},
            ],
        }
        if task == "classify":
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _content(response: httpx.Response) -> str:
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMUnavailableError(f"{response.status_code} from xAI")
        response.raise_for_status()
        content: str = response.json()["choices"][0]["message"]["content"]
        return content

    def complete(self, task: Task, text: str) -> str:
        chaos.current().attempts[task] += 1
        span = _Span(task)
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(self._url, headers=self._headers, json=self._payload(task, text))
            result = self._content(response)
        except Exception:
            span.close("error")
            raise
        span.close("ok")
        return result

    async def acomplete(self, task: Task, text: str) -> str:
        if self._async is None:
            self._async = httpx.AsyncClient(timeout=30)
        chaos.current().attempts[task] += 1
        span = _Span(task)
        try:
            response = await self._async.post(
                self._url, headers=self._headers, json=self._payload(task, text)
            )
            result = self._content(response)
        except asyncio.CancelledError:
            span.close("timeout")
            raise
        except Exception:
            span.close("error")
            raise
        span.close("ok")
        return result

    async def aclose(self) -> None:
        if self._async is not None:
            await self._async.aclose()
            self._async = None


class LLM:
    """Picks the provider per call. Injected failure scenarios always use the mock, so they repeat."""

    def __init__(self) -> None:
        self.mock = MockLLMClient()
        self._xai: XAIClient | None = None

    def _live(self) -> XAIClient | None:
        settings = get_settings()
        if settings.llm_provider != "xai" or chaos.current().scenario != "none":
            return None
        if self._xai is None:
            assert settings.xai_api_key is not None
            self._xai = XAIClient(
                settings.xai_api_key.get_secret_value(), settings.xai_model, settings.xai_base_url
            )
        return self._xai

    def complete(self, task: Task, text: str) -> str:
        live = self._live()
        return live.complete(task, text) if live else self.mock.complete(task, text)

    async def acomplete(self, task: Task, text: str) -> str:
        live = self._live()
        return await live.acomplete(task, text) if live else await self.mock.acomplete(task, text)

    async def aclose(self) -> None:
        if self._xai is not None:
            await self._xai.aclose()

    def describe(self) -> dict[str, str]:
        settings = get_settings()
        if settings.llm_provider == "xai":
            return {"provider": "xai", "model": settings.xai_model}
        return {"provider": "mock", "model": "keyword mock"}


llm = LLM()
