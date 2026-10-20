import asyncio
import logging
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.llm import LLMUnavailableError, Task, llm
from app.prod.models import LLMClassification, NonEmptyStr, Sentiment, TicketAnalysis, TicketIn

log = logging.getLogger("triage.service")

_summary = TypeAdapter(NonEmptyStr)
_sentiment = TypeAdapter(Sentiment)
FALLBACK_CLASSIFICATION = LLMClassification(category="other", priority="P2")


class LLMCallFailed(Exception):
    pass


def parse_summary(raw: str) -> str:
    return _summary.validate_python(raw)


def parse_sentiment(raw: str) -> Sentiment:
    return _sentiment.validate_python(raw.strip().strip(".!").lower())


async def call_llm[T](
    task: Task, text: str, parse: Callable[[str], T], settings: Settings
) -> tuple[T, int]:
    """One LLM call with a timeout, retries for transient errors, and validated output.

    Timeouts are not retried: a hung provider would just burn the latency budget twice.
    """
    reason = "unknown error"
    for attempt in range(1, settings.llm_max_attempts + 1):
        try:
            async with asyncio.timeout(settings.llm_timeout_s):
                raw = await llm.acomplete(task, text)
            return parse(raw), attempt
        except TimeoutError:
            log.warning("llm_timeout", extra={"task": task, "timeout_s": settings.llm_timeout_s})
            raise LLMCallFailed(f"{task}: no response within {settings.llm_timeout_s}s") from None
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
            await asyncio.sleep(settings.llm_retry_backoff_s * attempt)
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


async def analyze_ticket(ticket: TicketIn, trace_id: str, settings: Settings) -> TicketAnalysis:
    text = f"{ticket.subject}\n{ticket.body}"
    summary_r, classification_r, sentiment_r = await asyncio.gather(
        call_llm("summarize", text, parse_summary, settings),
        call_llm("classify", text, LLMClassification.model_validate_json, settings),
        call_llm("sentiment", text, parse_sentiment, settings),
        return_exceptions=True,
    )

    warnings: list[str] = []
    summary, summary_failed = _unwrap(summary_r, "summarize", None, warnings)
    classification, classify_failed = _unwrap(
        classification_r, "classify", FALLBACK_CLASSIFICATION, warnings
    )
    sentiment, sentiment_failed = _unwrap(sentiment_r, "sentiment", "unknown", warnings)

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
