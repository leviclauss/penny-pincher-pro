"""Sanity checks that the backend imports correctly."""

from __future__ import annotations


def test_core_imports() -> None:
    from core.config import get_settings
    from core.logging import configure_logging, get_logger
    from core.time import utcnow

    configure_logging(level="WARNING")
    log = get_logger(__name__)
    log.debug("smoke")

    settings = get_settings()
    assert settings.app_env in {"dev", "test", "staging", "prod"}
    assert utcnow().tzinfo is not None


def test_db_session_factory() -> None:
    from db import Base, get_engine, get_sessionmaker

    assert Base is not None
    assert get_engine() is not None
    assert get_sessionmaker() is not None
