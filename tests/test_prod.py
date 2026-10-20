import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app

TICKET = {
    "ticket_id": "TCK-1",
    "channel": "E-Mail",
    "customer_email": "priya@example.com",
    "subject": "Charged twice",
    "body": "I was charged twice for my order, please refund.",
}
URL = "/prod/tickets/analyze"


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(llm_timeout_s=1.0)
    with TestClient(app) as c:
        yield c  # type: ignore[misc]
    app.dependency_overrides.clear()


def test_happy_path_normalises_channel_and_classifies(client: TestClient) -> None:
    r = client.post(URL, json=TICKET)
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "billing"
    assert body["priority"] == "P2"
    assert body["degraded"] is False
    assert r.headers["X-Trace-Id"] == body["trace_id"]


def test_bad_input_returns_field_level_422(client: TestClient) -> None:
    bad = {**TICKET, "customer_email": "nope", "message": "renamed field"}
    del bad["body"]
    r = client.post(URL, json=bad)
    assert r.status_code == 422
    fields = {f["field"] for f in r.json()["fields"]}
    assert {"body", "customer_email", "message"} <= fields


def test_flaky_provider_recovers_with_retry(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "flaky"})
    assert r.status_code == 200
    assert any("recovered" in w for w in r.json()["warnings"])


def test_hung_call_degrades_instead_of_hanging(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "hang"})
    body = r.json()
    assert r.status_code == 200
    assert body["degraded"] is True
    assert body["sentiment"] == "unknown"
    assert body["summary"] is not None


def test_garbage_llm_output_goes_to_human_review(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "garbage"})
    body = r.json()
    assert body["needs_human_review"] is True
    assert body["category"] == "other"
