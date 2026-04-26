"""Request-scoped logging context.

Threads request_id and user_id into every log record so any line can be
traced back to the specific request and authenticated user that produced it.
"""

import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    fmt = "%(asctime)s [%(name)s] %(levelname)s rid=%(request_id)s uid=%(user_id)s %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # SQLAlchemy and uvicorn attach their own handlers at import time, which
    # would emit alongside ours in a different format. Strip those and force
    # propagation so every line goes through our single root handler.
    for name in (
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # We emit our own access line in RequestContextMiddleware, so silence the
    # uvicorn-level access log to avoid two lines per request.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
