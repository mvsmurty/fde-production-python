"""Lab checks for app/lab.py. Run: uv run pytest tests/test_lab.py

They all fail at the start. Each step in app/lab.py turns its own tests green.
"""

import asyncio
import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

URL = "/lab/tickets/analyze"
TICKET = {
    "ticket_id": "TCK-1",
    "channel": "whatsapp",
    "customer_email": "priya@example.com",
    "subject": "Charged twice",
    "body": "I was charged twice for my order, please refund ASAP.",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _timed_post(client: TestClient, chaos: str) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = client.post(URL, json=TICKET, headers={"X-Chaos": chaos})
    return response, time.perf_counter() - started


# Step 1: validate at the edge


def test_step1_bad_input_gets_422_naming_each_field(client: TestClient) -> None:
    bad = {k: v for k, v in TICKET.items() if k != "body"} | {"customer_email": "nope"}
    r = client.post(URL, json=bad)
    assert r.status_code == 422, f"expected 422 for bad input, got {r.status_code}"
    fields = {f["field"] for f in r.json()["fields"]}
    assert {"body", "customer_email"} <= fields


# Step 2: async calls, run together


def test_step2_three_llm_calls_run_in_parallel(client: TestClient) -> None:
    r, elapsed = _timed_post(client, "slow")
    assert r.status_code == 200
    assert elapsed < 2.0, f"took {elapsed:.1f} s: are the three calls running one after another?"


def test_step2_requests_do_not_block_each_other() -> None:
    async def three_at_once() -> tuple[list[httpx.Response], float]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            started = time.perf_counter()
            calls = [ac.post(URL, json=TICKET, headers={"X-Chaos": "load"}) for _ in range(3)]
            responses = await asyncio.gather(*calls)
            return list(responses), time.perf_counter() - started

    responses, elapsed = asyncio.run(three_at_once())
    assert all(r.status_code == 200 for r in responses)
    assert elapsed < 2.0, f"3 requests took {elapsed:.1f} s: is a blocking call freezing the server?"


# Step 3: never wait forever


def test_step3_hung_call_times_out_and_degrades(client: TestClient) -> None:
    r, elapsed = _timed_post(client, "hang")
    assert elapsed < 5.0, f"took {elapsed:.1f} s: is there a timeout around each LLM call?"
    assert r.status_code == 200
    body = r.json()
    assert body["sentiment"] == "unknown"
    assert body["degraded"] is True


# Stretch: every error says whose fault it is


def test_stretch_outage_returns_503_with_retry_after(client: TestClient) -> None:
    r, _ = _timed_post(client, "outage")
    assert r.status_code == 503, f"a provider outage should be 503, got {r.status_code}"
    assert "Retry-After" in r.headers
    assert r.json()["error"] == "llm_unavailable"


def test_stretch_bug_returns_500_with_trace_id_and_no_internals(client: TestClient) -> None:
    r, _ = _timed_post(client, "bug")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json"), "return the JSON error shape"
    assert r.json()["trace_id"] not in ("", "-"), "bind a trace id with Depends(bind_trace_id)"
    assert "choices" not in r.text, "don't leak exception details to the caller"
