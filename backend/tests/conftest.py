"""Shared pytest fixtures.

The reliability hooks added to ``scheduler.context.job_run`` (healthchecks.io
heartbeat ping, ``job_failed`` alert dispatch on the failure path) reach
out to the network and the global session in production. Both are
silenced here by default so test runs stay hermetic; ``real_healthchecks_ping``
and ``real_maybe_dispatch`` opt back in for the tests that exercise them.
"""

from __future__ import annotations

from typing import Any

import pytest

import alerts.triggers.job_failure as _job_failure_module
import core.healthchecks as _healthchecks_module

_REAL_PING = _healthchecks_module.ping
_REAL_MAYBE_DISPATCH = _job_failure_module.maybe_dispatch


def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture(autouse=True)
def _silence_reliability_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_healthchecks_module, "ping", _noop)
    monkeypatch.setattr(_job_failure_module, "maybe_dispatch", _noop)


@pytest.fixture
def real_healthchecks_ping(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(_healthchecks_module, "ping", _REAL_PING)
    return _REAL_PING


@pytest.fixture
def real_maybe_dispatch(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(_job_failure_module, "maybe_dispatch", _REAL_MAYBE_DISPATCH)
    return _REAL_MAYBE_DISPATCH
