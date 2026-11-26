"""Logging setup with secret redaction, plus error IDs for user-facing failures.

Users see a short error ID; the full traceback goes only into the protected log file.
"""
import logging
import re
import secrets
import time
import traceback
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

TOKEN_RE = re.compile(r"[MNO][A-Za-z\d_-]{23,28}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}")
KEYLIKE_RE = re.compile(r"(?i)(token|secret|api[_-]?key|authorization|password)(['\"\s:=]+)([^\s'\"]{6,})")

RECENT = deque(maxlen=200)       # (ts, error_id, module, summary)


def redact(text):
    text = TOKEN_RE.sub("[REDACTED-TOKEN]", str(text))
    return KEYLIKE_RE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def setup_logging(root_dir, name):
    """Discord library + bot logs -> logs/<name>.log (rotating). Console gets warnings and above."""
    log_dir = Path(root_dir) / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / f"{name}.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactingFilter())
    file_handler.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(RedactingFilter())
    console.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    return logging.getLogger(name)


def report_error(module, exc, context=""):
    """Log a full traceback under a fresh error ID and return the ID."""
    error_id = secrets.token_hex(4).upper()
    summary = redact(f"{type(exc).__name__}: {exc}")[:300]
    RECENT.append((time.time(), error_id, module, summary))
    logging.getLogger(f"bot.{module}").error(
        "Error %s in %s %s\n%s", error_id, module, redact(context)[:300],
        redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))
    return error_id


def record_problem(module, summary):
    """Record a non-exception failure (e.g. voice connect refused) for diagnostics."""
    error_id = secrets.token_hex(4).upper()
    RECENT.append((time.time(), error_id, module, redact(summary)[:300]))
    logging.getLogger(f"bot.{module}").warning("Problem %s in %s: %s", error_id, module, redact(summary))
    return error_id


def recent(limit=10, module=None, since_seconds=None):
    now = time.time()
    rows = [r for r in RECENT if (module is None or r[2] == module) and (since_seconds is None or now - r[0] <= since_seconds)]
    return rows[-limit:][::-1]


def unwrap(error):
    """Follow .original chains (CommandInvokeError, HybridCommandError, app CommandInvokeError)."""
    seen = 0
    while hasattr(error, "original") and getattr(error, "original") is not None and seen < 5:
        error = error.original
        seen += 1
    return error
