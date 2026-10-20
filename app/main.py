import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.llm import llm
from app.naive import router as naive_router
from app.prod.observability import configure_logging, trace_id_var
from app.prod.routes import router as prod_router

log = logging.getLogger("triage.app")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.demo_mode:
        from app.demo.routes import install_log_capture

        install_log_capture()
    configure_logging(settings.log_level)
    log.info("startup", extra={"demo_mode": settings.demo_mode, **llm.describe()})
    yield
    await llm.aclose()
    log.info("shutdown")


app = FastAPI(title="Ticket Triage", lifespan=lifespan)
app.include_router(naive_router)
app.include_router(prod_router)


@app.exception_handler(RequestValidationError)
async def validation_failed(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {"field": ".".join(str(p) for p in err["loc"][1:]) or "body", "message": err["msg"]}
        for err in exc.errors()
    ]
    log.warning("request_rejected", extra={"fields": [f["field"] for f in fields]})
    return JSONResponse(
        status_code=422,
        content={"error": "validation_failed", "fields": fields, "trace_id": trace_id_var.get()},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if settings.demo_mode:
    from app.demo.middleware import ChaosMiddleware
    from app.demo.routes import router as demo_router

    app.add_middleware(ChaosMiddleware)
    app.include_router(demo_router)
    app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True))
