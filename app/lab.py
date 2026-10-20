"""Lab: make this endpoint survive production.

It starts as a copy of app/naive.py. Work through the TODOs in order, and after each step run:

    uv run pytest tests/test_lab.py

app/prod/ has a complete reference. Try each step yourself first.
"""

import json

from fastapi import APIRouter, Request

from app.llm import llm

router = APIRouter()


# TODO Step 1 (incident 07): validate at the edge.
#   Replace `request: Request` with `ticket: TicketIn` (from app.prod.models) and build the
#   text from ticket.subject and ticket.body. Bad input should then get a 422 naming each field.
@router.post("/lab/tickets/analyze")
async def analyze_ticket(request: Request):
    payload = await request.json()
    print("Got request:", dict(request.headers), payload)

    text = payload["subject"] + "\n" + payload["body"]

    # TODO Step 2 (incident 01): stop blocking the event loop, and run the calls together.
    #   llm.complete blocks every other request. Use `await llm.acomplete(...)` instead, and
    #   start all three calls at once with asyncio.gather(..., return_exceptions=True).
    #
    # TODO Step 3 (incident 05): never wait forever.
    #   Wrap each call in `async with asyncio.timeout(3):`. If a call times out, answer anyway:
    #   use "unknown" for the sentiment and add "degraded": True to the response.
    summary = llm.complete("summarize", text)
    classification = json.loads(llm.complete("classify", text))
    sentiment = llm.complete("sentiment", text)

    # TODO Stretch (incident 06): every error says whose fault it is.
    #   - Add `trace_id: Annotated[str, Depends(bind_trace_id)]` (from app.prod.routes).
    #   - If every call failed with LLMUnavailableError (from app.llm), raise
    #     APIError(503, "llm_unavailable", ..., headers={"Retry-After": "30"}).
    #   - For anything unexpected, call log.exception(...) and raise
    #     APIError(500, "internal_error", ...). APIError is in app.prod.errors.
    print("Done:", summary, classification, sentiment)
    return {
        "ticket_id": payload.get("ticket_id"),
        "summary": summary,
        "category": classification["category"],
        "priority": classification["priority"],
        "sentiment": sentiment,
    }
