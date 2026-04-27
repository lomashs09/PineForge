"""Thin wrapper around sentry_sdk.metrics that swallows any failure.

Every call site emits one metric line. A misconfigured or unreachable Sentry
must never break a request, a webhook, or the bot lifecycle — hence the broad
try/except around each call.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def count(name: str, value: float = 1, attributes: Optional[dict] = None) -> None:
    try:
        from sentry_sdk import metrics
        metrics.count(name, value, attributes=attributes or {})
    except Exception as e:
        logger.debug("metrics.count(%s) failed: %s", name, e)


def gauge(name: str, value: float, attributes: Optional[dict] = None) -> None:
    try:
        from sentry_sdk import metrics
        metrics.gauge(name, value, attributes=attributes or {})
    except Exception as e:
        logger.debug("metrics.gauge(%s) failed: %s", name, e)


def distribution(
    name: str,
    value: float,
    unit: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> None:
    try:
        from sentry_sdk import metrics
        metrics.distribution(name, value, unit=unit, attributes=attributes or {})
    except Exception as e:
        logger.debug("metrics.distribution(%s) failed: %s", name, e)
