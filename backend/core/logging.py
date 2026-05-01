"""structlog configuration. Call ``configure_logging()`` once at process start."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

_state: dict[str, bool] = {"configured": False}


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    if _state["configured"]:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _state["configured"] = True


def get_logger(name: str | None = None, **initial: Any) -> structlog.stdlib.BoundLogger:
    """Module-level logger. Pass ``__name__``."""
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    if initial:
        logger = logger.bind(**initial)
    return logger  # type: ignore[no-any-return]
