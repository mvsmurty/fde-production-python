"""Version 0: works in the notebook, works in the demo.

Every line here is something we have seen in real code. Spot the problems before the demo.
"""

import json

from fastapi import APIRouter, Request

from app.llm import llm

router = APIRouter()

ticket_history = []


@router.post("/naive/tickets/analyze")
async def analyze_ticket(request: Request):
    payload = await request.json()
    print("Got request:", dict(request.headers), payload)
    ticket_history.append(payload)

    text = payload["subject"] + "\n" + payload["body"]

    summary = llm.complete("summarize", text)
    classification = json.loads(llm.complete("classify", text))
    sentiment = llm.complete("sentiment", text)

    print("Done:", summary, classification, sentiment)
    return {
        "ticket_id": payload.get("ticket_id"),
        "summary": summary,
        "category": classification["category"],
        "priority": classification["priority"],
        "sentiment": sentiment,
    }
