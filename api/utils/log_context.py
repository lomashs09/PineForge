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

    # Uvicorn's default access log duplicates ours; keep only warnings+
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # SQLAlchemy echo at INFO floods journals with raw SQL; keep at WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
