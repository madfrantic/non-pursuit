"""
App-wide error logging.

Before this, failures in background operations (Playwright automation, the
IP/ISP lookup, PDF generation) were either silently swallowed or only ever
visible as a Streamlit traceback in the terminal running the app -- nothing
persisted once that terminal scrolled past it. get_logger() gives every
module the same rotating file handler so a user can actually find out what
went wrong after the fact, without any of it going to a server this app
doesn't have.

The log can end up holding identifying details (a name in an exception
message, a broker URL) the same way the rest of data/ can, so it's
gitignored and never uploaded anywhere -- see config.LOG_PATH.
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config

_configured = False


def _configure_root():
    global _configured
    if _configured:
        return
    # Ensure the log directory exists; if creation fails (e.g., read‑only FS), fall back to console logging.
    try:
        Path(config.LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            config.LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger("non_pursuit")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except OSError as e:
        # If we cannot write to the log file (common in sandbox), use a simple stream handler.
        root = logging.getLogger("non_pursuit")
        root.setLevel(logging.INFO)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(stream_handler)
        # Log the fallback event.
        root.debug(f"Logging to file failed ({e}); using console stream handler.")
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """A logger under the shared 'non_pursuit' namespace, writing to
    config.LOG_PATH. Safe to call repeatedly (e.g. once per Streamlit
    rerun) -- the file handler is only attached once per process."""
    _configure_root()
    return logging.getLogger(f"non_pursuit.{name}")
