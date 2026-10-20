import asyncio
import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from app.config import Settings, get_settings
from app.prod.errors import APIError
from app.prod.models import TicketAnalysis, TicketIn
from app.prod.observability import trace_id_var
from app.prod.ratelimit import rate_limit
from app.prod.service import LLMOutage, analyze_ticket

log = logging.getLogger("triage.api")


async def bind_trace_id(
    response: Response, x_request_id: Annotated[str | None, Header()] = None
) -> str:
    trace_id = x_request_id or uuid.uuid4().hex[:16]
    trace_id_var.set(trace_id)
    response.headers["X-Trace-Id"] = trace_id
    return trace_id


def get_llm_slots(request: Request) -> asyncio.Semaphore:
    slots: asyncio.Semaphore = request.app.state.llm_slots
    return slots


router = APIRouter(
    prefix="/prod",
    tags=["production"],
    dependencies=[Depends(bind_trace_id), Depends(rate_limit)],
)


@router.post("/tickets/analyze")
async def analyze(
    ticket: TicketIn,
    trace_id: Annotated[str, Depends(bind_trace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    slots: Annotated[asyncio.Semaphore, Depends(get_llm_slots)],
) -> TicketAnalysis:
    started = time.perf_counter()
    log.info(
        "ticket_received",
        extra={
            "ticket_id": ticket.ticket_id,
            "channel": ticket.channel,
            "customer_email": ticket.customer_email,
            "body_chars": len(ticket.body),
        },
    )
    try:
        result = await analyze_ticket(ticket, trace_id, settings, slots)
    except LLMOutage as exc:
        log.error("llm_outage", extra={"ticket_id": ticket.ticket_id, "reason": str(exc)})
        raise APIError(
            503, "llm_unavailable", "The language model is unavailable. Retry shortly.",
            headers={"Retry-After": "30"},
        ) from None
    except Exception:
        log.exception("ticket_analysis_failed", extra={"ticket_id": ticket.ticket_id})
        raise APIError(
            500, "internal_error",
            "Something went wrong on our side. Quote the trace id when reporting it.",
        ) from None
    log.info(
        "ticket_analyzed",
        extra={
            "ticket_id": ticket.ticket_id,
            "priority": result.priority,
            "degraded": result.degraded,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return result
