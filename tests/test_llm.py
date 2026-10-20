import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import LLMUnavailableError, XAIClient


def _client_with(handler: httpx.MockTransport) -> XAIClient:
    client = XAIClient("test-key", "grok-test", "https://api.x.ai/v1")
    client._async = httpx.AsyncClient(transport=handler)
    return client


def test_xai_returns_message_content_and_sends_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "negative"}}]})

    client = _client_with(httpx.MockTransport(handler))
    assert asyncio.run(client.acomplete("sentiment", "angry")) == "negative"


@pytest.mark.parametrize("status", [429, 503])
def test_xai_rate_limit_and_outage_are_retryable(status: int) -> None:
    client = _client_with(httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(LLMUnavailableError):
        asyncio.run(client.acomplete("summarize", "text"))


def test_xai_provider_without_key_fails_at_startup() -> None:
    with pytest.raises(ValidationError, match="TRIAGE_XAI_API_KEY"):
        Settings(llm_provider="xai", xai_api_key=None, _env_file=None)  # type: ignore[call-arg]
