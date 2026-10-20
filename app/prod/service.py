import asyncio
import logging
import random
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.llm import LLMRateLimitedError, LLMUnavailableError, Task, llm
from app.prod.models import LLMClassification, NonEmptyStr, Sentiment, TicketAnalysis, TicketIn

log = logging.getLogger("triage.service")

_summary = TypeAdapter(NonEmptyStr)
_sentiment = TypeAdapter(Sentiment)
FALLBACK_CLASSIFICATION = LLMClassification(category="other", priority="P2")


class LLMCallFailed(Exception):
    """One LLM call failed in an expected way (timeout, provider down, bad output)."""


class LLMOutage(Exception):
    """Every LLM call failed: there is nothing useful to return."""


def parse_summary(raw: str) -> str:
    return _summary.validate_python(raw)


def parse_sentiment(raw: str) -> Sentiment:
    return _sentiment.validate_python(raw.strip().strip(".!").lower())


async def call_llm[T](
    task: Task,
    text: str,
    parse: Callable[[str], T],
    settings: Settings,
    slots: asyncio.Semaphore,
) -> tuple[T, int]:
    """One LLM call: concurrency-capped, time-limited, retried when that can help, output validated.

    Timeouts are not retried: a hung provider would just burn the latency budget twice.
    Anything unexpected (a bug) is not caught here, so it surfaces instead of being hidden.
    """
    reason = "unknown error"
    for attempt in range(1, settings.llm_max_attempts + 1):
        wait = settings.llm_retry_backoff_s * attempt * random.uniform(0.8, 1.2)
        try:
            async with slots:
                async with asyncio.timeout(settings.llm_timeout_s):
                    raw = await llm.acomplete(task, text)
            return parse(raw), attempt
        except TimeoutError:
            log.warning("llm_timeout", extra={"task": task, "timeout_s": settings.llm_timeout_s})
            raise LLMCallFailed(f"{task}: no response within {settings.llm_timeout_s}s") from None
        except LLMRateLimitedError as exc:
            if exc.retry_after > settings.llm_max_retry_after_s:
                raise LLMCallFailed(f"{task}: rate limited for {exc.retry_after:g}s") from None
            reason, wait = "rate limited", exc.retry_after
            log.warning(
                "llm_rate_limited",
                extra={"task": task, "attempt": attempt, "retry_after_s": exc.retry_after},
            )
        except LLMUnavailableError as exc:
            reason = "provider unavailable"
            log.warning("llm_unavailable", extra={"task": task, "attempt": attempt, "error": str(exc)})
        except ValidationError as exc:
            reason = "output failed validation"
            log.warning(
                "llm_output_invalid",
                extra={"task": task, "attempt": attempt, "errors": exc.error_count()},
            )
        if attempt < settings.llm_max_attempts:
            await asyncio.sleep(wait)
    raise LLMCallFailed(f"{task}: {reason} after {settings.llm_max_attempts} attempts")


def _unwrap[T](
    result: tuple[T, int] | BaseException, task: str, fallback: T, warnings: list[str]
) -> tuple[T, bool]:
    """Returns (value, used_fallback). Unexpected exceptions are re-raised, not swallowed."""
    if isinstance(result, LLMCallFailed):
        warnings.append(str(result))
        return fallback, True
    if isinstance(result, BaseException):
        raise result
    value, attempts = result
    if attempts > 1:
        warnings.append(f"{task}: recovered after {attempts - 1} retry")
    return value, False


async def analyze_ticket(
    ticket: TicketIn, trace_id: str, settings: Settings, slots: asyncio.Semaphore
) -> TicketAnalysis:
    text = f"{ticket.subject}\n{ticket.body}"
    summary_r, classification_r, sentiment_r = await asyncio.gather(
        call_llm("summarize", text, parse_summary, settings, slots),
        call_llm("classify", text, LLMClassification.model_validate_json, settings, slots),
        call_llm("sentiment", text, parse_sentiment, settings, slots),
        return_exceptions=True,
    )

    warnings: list[str] = []
    summary, summary_failed = _unwrap(summary_r, "summarize", None, warnings)
    classification, classify_failed = _unwrap(
        classification_r, "classify", FALLBACK_CLASSIFICATION, warnings
    )
    sentiment, sentiment_failed = _unwrap(sentiment_r, "sentiment", "unknown", warnings)
    if summary_failed and classify_failed and sentiment_failed:
        raise LLMOutage("; ".join(warnings))

    return TicketAnalysis(
        ticket_id=ticket.ticket_id,
        summary=summary,
        category=classification.category,
        priority=classification.priority,
        sentiment=sentiment,
        needs_human_review=classify_failed,
        degraded=summary_failed or classify_failed or sentiment_failed,
        warnings=warnings,
        trace_id=trace_id,
    )
