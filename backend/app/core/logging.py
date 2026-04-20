"""Structured logging with context-variable injection."""

from __future__ import annotations

import json
import logging
import os
import contextvars
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"  # type: ignore[attr-defined]
        record.job_id = job_id_ctx.get() or "-"  # type: ignore[attr-defined]
        record.module_name = record.name  # type: ignore[attr-defined]
        record.short_file = record.pathname.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]  # type: ignore[attr-defined]
        return True


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "file": getattr(record, "short_file", ""),
            "line": record.lineno,
            "request_id": getattr(record, "request_id", None),
            "job_id": getattr(record, "job_id", None),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class _ColorFormatter(logging.Formatter):
    _COLORS = {
        "DEBUG": "\033[96m",
        "INFO": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "CRITICAL": "\033[95m",
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        c = self._COLORS.get(record.levelname, self._RESET)
        ts = datetime.now(timezone.utc).isoformat()
        return (
            f"{c}{record.levelname}{self._RESET} | {ts} "
            f"| {getattr(record, 'short_file', '')}:{record.lineno} "
            f"| req:{getattr(record, 'request_id', '-')} "
            f"job:{getattr(record, 'job_id', '-')} "
            f"| {record.getMessage()}"
        )


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: str = "./logs",
    log_file: str = "application.log",
    max_mb: int = 10,
    backup_count: int = 10,
    json_logs: bool = True,
) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    ctx_filter = _ContextFilter()

    console = logging.StreamHandler()
    console.setFormatter(_ColorFormatter())
    console.addFilter(ctx_filter)
    root.addHandler(console)

    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(log_dir, log_file),
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backup_count,
    )
    fh.setFormatter(_JSONFormatter() if json_logs else _ColorFormatter())
    fh.addFilter(ctx_filter)
    root.addHandler(fh)
