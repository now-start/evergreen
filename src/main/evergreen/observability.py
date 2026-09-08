"""Operational logs: fixed event names, timings, and exception types only."""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter


def configure_logging() -> None:
    # Preserve existing handlers, including the platform's OpenTelemetry handler.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    level = os.getenv("LOGGING_LEVEL_EVERGREEN", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"
    logging.getLogger("evergreen").setLevel(level)
    for name in ("upbit", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def operation(logger: logging.Logger, event: str, *, level: int = logging.DEBUG) -> Iterator[None]:
    """Instrument a fixed operation without logging exception payloads or changing control flow."""
    start = perf_counter()
    logger.log(level, "event=%s status=started", event)
    try:
        yield
    except Exception as error:
        logger.error(
            "event=%s status=failed error_type=%s duration_ms=%.1f",
            event,
            type(error).__name__,
            (perf_counter() - start) * 1000,
        )
        raise
    else:
        logger.log(
            level,
            "event=%s status=completed duration_ms=%.1f",
            event,
            (perf_counter() - start) * 1000,
        )
