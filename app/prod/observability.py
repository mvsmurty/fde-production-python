import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)bearer\s+[\w.\-]+"), "Bearer <redacted>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"(?<!\w)(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\w)"), "<phone>"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "<card>"),
]

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the request's trace id, PII scrubbed as a last line of defence."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, default=str))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("triage")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False
