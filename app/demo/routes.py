"""Lets the UI show what the server printed and how long each LLM call took."""

import gc
import logging
import sys
import threading
from collections import deque
from typing import TextIO

import psutil
from fastapi import APIRouter

from app.demo.middleware import spans_by_request
from app.llm import llm

router = APIRouter(prefix="/demo", include_in_schema=False)

_lines: deque[tuple[int, str]] = deque(maxlen=3000)
_seq = 0
_lock = threading.Lock()
_installed = False
_MAX_LINE = 1500


def _record(text: str) -> None:
    global _seq
    with _lock:
        for line in text.splitlines():
            if line.strip():
                _seq += 1
                if len(line) > _MAX_LINE:
                    line = f"{line[:_MAX_LINE]}… [{len(line) - _MAX_LINE:,} more characters]"
                _lines.append((_seq, line))


class _Tee:
    def __init__(self, original: TextIO):
        self._original = original
        self._partial = ""

    def write(self, text: str) -> int:
        self._original.write(text)
        self._partial += text
        if "\n" in self._partial:
            complete, self._partial = self._partial.rsplit("\n", 1)
            _record(complete)
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self._original, name)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _record(self.format(record))


def install_log_capture() -> None:
    """Mirror stdout (print + JSON logs) and uvicorn's tracebacks into a buffer the UI can read."""
    global _installed
    if _installed:
        return
    sys.stdout = _Tee(sys.stdout)
    handler = _BufferHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    logging.getLogger("uvicorn.error").addHandler(handler)
    _installed = True


@router.get("/logs")
async def logs(after: int = 0) -> dict[str, object]:
    with _lock:
        lines = [{"seq": s, "text": t} for s, t in _lines if s > after]
        last = _seq
    return {"lines": lines, "last_seq": last}


@router.get("/info")
async def info() -> dict[str, str]:
    return llm.describe()


@router.get("/memory")
async def memory() -> dict[str, float]:
    from app.naive import ticket_history

    gc.collect()
    rss = psutil.Process().memory_info().rss
    kept_chars = sum(len(str(t.get("body", ""))) for t in ticket_history)
    return {
        "rss_mb": round(rss / 1024 / 1024, 1),
        "naive_kept": len(ticket_history),
        "naive_kept_mb": round(kept_chars / 1024 / 1024, 1),
    }


@router.get("/spans/{request_id}")
async def spans(request_id: str) -> dict[str, object]:
    state = spans_by_request.get(request_id)
    return {"spans": state.spans if state else []}
