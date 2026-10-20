from collections import OrderedDict

from starlette.types import ASGIApp, Receive, Scope, Send

from app.demo.chaos import SCENARIOS, ChaosState, Scenario, chaos_var

_MAX_TRACKED = 500
spans_by_request: OrderedDict[str, ChaosState] = OrderedDict()


class ChaosMiddleware:
    """Reads `X-Chaos` to pick a failure scenario and records LLM call spans per `X-Demo-Request-Id`.

    Spans are kept server-side (not in a response header) so they survive 500s and client timeouts.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        raw = headers.get("x-chaos", "none")
        scenario: Scenario = raw if raw in SCENARIOS else "none"
        state = ChaosState(scenario=scenario)
        if request_id := headers.get("x-demo-request-id"):
            spans_by_request[request_id] = state
            while len(spans_by_request) > _MAX_TRACKED:
                spans_by_request.popitem(last=False)
        token = chaos_var.set(state)
        try:
            await self.app(scope, receive, send)
        finally:
            chaos_var.reset(token)
