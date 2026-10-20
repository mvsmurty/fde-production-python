import logging
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import Settings, get_settings
from app.prod.models import TicketAnalysis, TicketIn
from app.prod.observability import trace_id_var
from app.prod.service import analyze_ticket

log = logging.getLogger("triage.api")

router = APIRouter(prefix="/prod", tags=["production"])


async def bind_trace_id(
    response: Response, x_request_id: Annotated[str | None, Header()] = None
) -> str:
    trace_id = x_request_id or uuid.uuid4().hex[:16]
    trace_id_var.set(trace_id)
    response.headers["X-Trace-Id"] = trace_id
    return trace_id


@router.post("/tickets/analyze")
async def analyze(
    ticket: TicketIn,
    trace_id: Annotated[str, Depends(bind_trace_id)],
    settings: Annotated[Settings, Depends(get_settings)],
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
        result = await analyze_ticket(ticket, trace_id, settings)
    except Exception:
        log.exception("ticket_analysis_failed", extra={"ticket_id": ticket.ticket_id})
        raise HTTPException(
            status_code=500, detail={"error": "internal_error", "trace_id": trace_id}
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
