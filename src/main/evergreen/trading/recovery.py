"""One retry policy for the shared CLI/API trading supervisor, never for order POSTs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite

import httpx
from sqlalchemy.exc import DBAPIError, TimeoutError
from upbit import APIConnectionError, APIStatusError

from evergreen.database.connection import ExecutionLockError


class TradingRejected(ValueError):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class Recovery:
    reason: str
    minimum_delay: float = 0


def _retry_after(error: APIStatusError) -> float:
    value = error.response.headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if isfinite(seconds) else 0


def classify(error: Exception) -> Recovery:
    if isinstance(error, TradingRejected):
        return Recovery(error.reason)
    if isinstance(error, ExecutionLockError):
        return Recovery("database_lock_unavailable")
    if isinstance(error, (APIConnectionError, httpx.TransportError)):
        return Recovery("exchange_connection")
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return Recovery("exchange_authentication", 60)
        if error.status_code == 404:
            # An unresolved write-ahead intent remains pending; never POST it again.
            return Recovery("exchange_not_found", 60)
        if error.status_code in {408, 418, 429} or error.status_code >= 500:
            return Recovery(
                "exchange_unavailable",
                max(_retry_after(error), 60 if error.status_code in {418, 429} else 0),
            )
        return Recovery("exchange_request_rejected", 60)
    if isinstance(error, TimeoutError):
        return Recovery("database_timeout")
    if isinstance(error, DBAPIError):
        code = error.orig.args[0] if error.orig is not None and error.orig.args else None
        if error.connection_invalidated or code in {1205, 1213, 2002, 2003, 2006, 2013}:
            return Recovery("database_connection")
        return Recovery("database_error", 60)
    # Do not infer safety from exception messages (which can also contain credentials).
    return Recovery("revalidation_required", 60)


def delay(attempt: int, recovery: Recovery) -> float:
    return max(recovery.minimum_delay, float((5, 10, 20, 40, 60)[min(max(attempt - 1, 0), 4)]))
