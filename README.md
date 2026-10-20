# Ticket Triage: naive vs production

One FastAPI app, two endpoints that do the same job: analyse a support ticket with three LLM calls (summary, category/priority, sentiment).

- `POST /naive/tickets/analyze`: [app/naive.py](app/naive.py), written the way it looks in a notebook
- `POST /prod/tickets/analyze`: [app/prod/](app/prod/), Pydantic at the edge, async calls with timeouts, retries that honour `Retry-After`, a cap on concurrent LLM calls, a per-client rate limit, one error shape (422 / 429 / 503 / 500, each with a trace id), validated LLM output, JSON logs with redaction

The LLM is a mock ([app/llm.py](app/llm.py)), so no API key is needed and every run is repeatable.

## Run the demo

```bash
uv sync
uv run uvicorn app.main:app --port 8077
```

Open http://localhost:8077, pick a scenario (keys 1–9 and 0, or ↑ ↓), press Enter. The out-of-memory scenario prints 4 MB lines from the naive endpoint, so you may want `> /dev/null` on the uvicorn command.

Run uvicorn with a single worker (the default). The traffic-spike and hang scenarios show the naive endpoint freezing the whole server, which a multi-worker setup would partly hide.

Each scenario is an incident that worked in development and failed in production:

| # | Incident | Naive | Production |
|---|---|---|---|
| 1 | Works in development | 200 | 200 |
| 2 | Slow provider | 3.6 s (sequential) | 1.2 s (parallel) |
| 3 | Traffic spike, 5 users | Served one at a time, server frozen | All served together |
| 4 | Provider rate limit (429) | 500 | Waits for `Retry-After`, succeeds |
| 5 | Client floods your API | Accepts all 12 (36 LLM calls) | 5 accepted, 7 rejected with 429 |
| 6 | Provider hangs | No response, server frozen | 3 s, partial result flagged `degraded` |
| 7 | Provider outage | 500 | 503 with `Retry-After` |
| 8 | Bug in our code | Bare 500, no way to find the trace | 500 with trace id, stack trace logged under it |
| 9 | Unexpected input | 500, no explanation | 422 naming each bad field |
| 10 | Bad model output | 200 with garbage | Validated, falls back, flags for a human |
| 11 | Logs and secrets | Token, email, phone in logs | JSON with trace id, PII scrubbed |
| 12 | Out of memory (10 × 4 MB tickets) | Accepts them, keeps every one in a global list | 413 before the body is read |

Scenarios are triggered with an `X-Chaos` request header that the mock LLM reads. That header, the `/demo` endpoints and the UI exist only when `TRIAGE_DEMO_MODE=true` (the default here).

## Lab

`app/lab.py` (`POST /lab/tickets/analyze`) starts as a copy of the naive endpoint, with TODOs:

1. Validate at the edge with `TicketIn` (bad input gets a 422)
2. Use `await llm.acomplete` with `asyncio.gather` (no blocking, calls run together)
3. Wrap each call in `asyncio.timeout(3)` and degrade instead of hanging
4. Stretch: 503 for a provider outage, a 500 with a trace id for bugs

Check your progress; the tests start red and turn green step by step:

```bash
uv run pytest tests/test_lab.py
```

To see it in the UI, choose **Lab (your code)** in the "Compare … with production" menu at the top. The normal `uv run pytest` skips the lab tests. A reference solution is on the `lab-solution` branch.

## Using a real LLM (xAI Grok)

The mock is the default. To answer with Grok instead:

```bash
cp .env.example .env   # then put your key in TRIAGE_XAI_API_KEY
uv run uvicorn app.main:app --port 8077
```

The top bar shows which model is answering. Only scenarios with no injected failure (1, 9 and 11) go to Grok; the failure scenarios always use the mock so they behave the same every run. The naive endpoint calls Grok with a blocking `httpx.Client`, while production uses one shared `httpx.AsyncClient` with a timeout, and retries on 429/5xx.

`.env` is git-ignored. Never commit it, and never paste the key into chat or code.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```

## Tool-calling teaser

```bash
uv run python -m app.prod.tools
```

Prints the JSON schema an LLM provider would receive for a `lookup_customer` tool, generated from a Pydantic model.
