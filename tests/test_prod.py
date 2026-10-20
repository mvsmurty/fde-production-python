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


def _client(**overrides: object) -> TestClient:
    values: dict[str, object] = {"llm_timeout_s": 1.0, "rate_limit_requests": 1000, **overrides}
    settings = Settings(**values)  # type: ignore[arg-type]
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    with _client() as c:
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


def test_provider_429_is_retried_after_retry_after(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "ratelimit"})
    assert r.status_code == 200
    assert r.json()["degraded"] is False
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


def test_provider_outage_returns_503_with_retry_after(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "outage"})
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "30"
    assert r.json()["error"] == "llm_unavailable"


def test_bug_returns_500_with_trace_id_and_no_internals(client: TestClient) -> None:
    r = client.post(URL, json=TICKET, headers={"X-Chaos": "bug"})
    body = r.json()
    assert r.status_code == 500
    assert body["error"] == "internal_error"
    assert body["trace_id"] == r.headers["X-Trace-Id"]
    assert "choices" not in r.text


def test_oversized_body_is_rejected_with_413_before_parsing(client: TestClient) -> None:
    huge = {**TICKET, "body": "x" * 200_000}
    r = client.post(URL, json=huge)
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"


def test_rate_limiter_forgets_idle_clients() -> None:
    from app.prod.ratelimit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(max_clients=3)
    for i in range(50):
        limiter.retry_after(f"client-{i}", limit=5, window_s=0.0)
    assert len(limiter._hits) <= 3


def test_client_over_its_limit_gets_429() -> None:
    with _client(rate_limit_requests=2) as c:
        headers = {"X-Client-Id": "flooder", "X-Chaos": "flood"}
        codes = [c.post(URL, json=TICKET, headers=headers).status_code for _ in range(4)]
        last = c.post(URL, json=TICKET, headers=headers)
    app.dependency_overrides.clear()
    assert codes == [200, 200, 429, 429]
    assert int(last.headers["Retry-After"]) >= 1
    assert last.json()["error"] == "rate_limited"
