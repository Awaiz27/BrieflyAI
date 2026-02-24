import os
import json
import contextvars
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from configs.constants import LOG_LEVEL, LOG_DIR, LOG_FILE_NAME, MAX_LOG_SIZE_MB, BACKUP_COUNT


request_id_ctx = contextvars.ContextVar("request_id", default=None)
job_id_ctx = contextvars.ContextVar("job_id", default=None)


def set_context(request_id=None, job_id=None):
    if request_id:
        request_id_ctx.set(request_id)
    if job_id:
        job_id_ctx.set(job_id)

def get_context():
    return {
        "request_id": request_id_ctx.get(),
        "job_id": job_id_ctx.get(),
    }

def clear_context():
    request_id_ctx.set(None)
    job_id_ctx.set(None)

def utc_now():
    return datetime.now(timezone.utc).isoformat()

class ContextFilter(logging.Filter):
    """Injects context into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get() or "-"
        record.job_id = job_id_ctx.get() or "-"
        record.module_name = record.name   # logger's __name__
        record.file_name = record.pathname.split("/")[-1]
        return True
    


class JSONFormatter(logging.Formatter):
    """Structured JSON logs suitable for production & log ingestion."""
    
    def format(self, record: logging.LogRecord) -> str:
        log = {
            "timestamp": utc_now(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module_name,
            "file": record.file_name,
            "line": record.lineno,

            # request/job context
            "request_id": getattr(record, "request_id", None),
            "job_id": getattr(record, "job_id", None),
        }

        # include stack trace if present
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)

        return json.dumps(log)

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[96m",
        "INFO": "\033[92m",
        "NOTICE": "\033[94m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "CRITICAL": "\033[95m",
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]

        timestamp = utc_now()

        # rich developer-friendly format
        return (
            f"{color}{record.levelname}{reset} "
            f"| {timestamp} "
            f"| {record.file_name}:{record.lineno} "
            f"| {record.module_name} "
            f"| req:{record.request_id} job:{record.job_id} "
            f"| {record.getMessage()}"
        )


def setup_logging( log_level: str = LOG_LEVEL , json_logs: bool = False):
    """Call once per application or service."""
    
    root = logging.getLogger()
    root.setLevel(log_level)

    if root.handlers:
        return root

    root.handlers.clear()

    os.makedirs(LOG_DIR, exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(log_level)
    console.setFormatter(ColorFormatter())
    console.addFilter(ContextFilter())
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        f"{LOG_DIR}/{LOG_FILE_NAME}",
        maxBytes=MAX_LOG_SIZE_MB * 1024 * 1024,
        backupCount=BACKUP_COUNT,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(JSONFormatter() if json_logs else ColorFormatter())
    file_handler.addFilter(ContextFilter())
    
    root.addHandler(file_handler)

    return root