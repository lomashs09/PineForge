"""Request context middleware: X-Request-Id, access log, Sentry scoping."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..utils.log_context import request_id_var, user_id_var

logger = logging.getLogger("api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Honor an upstream X-Request-Id (proxy/client traceability), else mint one.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token_rid = request_id_var.set(request_id)
        token_uid = user_id_var.set("-")
        request.state.request_id = request_id

        try:
            import sentry_sdk
            sentry_sdk.set_tag("request_id", request_id)
        except Exception:
            pass

        start = time.perf_counter()
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            # The exception will propagate; log it as a 500 access line and re-raise
            # so Sentry's middleware can capture it.
            logger.exception(
                "%s %s -> 500 (unhandled) client=%s",
                request.method, request.url.path,
                request.client.host if request.client else "-",
            )
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            client = request.client.host if request.client else "-"
            logger.info(
                "%s %s -> %d %dms client=%s",
                request.method, request.url.path, status_code, duration_ms, client,
            )
            _emit_metrics(request, status_code, duration_ms)
            request_id_var.reset(token_rid)
            user_id_var.reset(token_uid)


def _emit_metrics(request: Request, status_code: int, duration_ms: int) -> None:
    """Emit per-request count + duration to Sentry metrics.

    Uses the route template (e.g. /api/bots/{bot_id}) instead of the raw path so
    cardinality stays bounded.
    """
    try:
        from sentry_sdk import metrics

        route = request.scope.get("route")
        path_template = getattr(route, "path", request.url.path) if route else request.url.path
        attributes = {
            "method": request.method,
            "path": path_template,
            "status": status_code,
        }
        metrics.count("api.requests", 1, attributes=attributes)
        metrics.distribution(
            "api.request.duration_ms", duration_ms, unit="millisecond", attributes=attributes,
        )
    except Exception:
        pass
