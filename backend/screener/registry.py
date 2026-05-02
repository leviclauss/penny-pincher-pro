"""Filter registry: maps the ``"id"`` string in a config to a Filter class.

Filter modules register themselves at import time via ``@register``. The
pipeline imports ``screener.filters`` (which imports each submodule) so the
registry is populated before any config is resolved. Re-registering the
same id raises — duplicate ids would silently make config behavior depend
on import order.
"""

from __future__ import annotations

from typing import TypeVar

from screener.filters.base import Filter

FILTER_REGISTRY: dict[str, type[Filter]] = {}

F = TypeVar("F", bound=type[Filter])


def register(cls: F) -> F:
    """Class decorator: register ``cls`` under its ``id`` attribute."""
    filter_id = getattr(cls, "id", None)
    if not isinstance(filter_id, str) or not filter_id:
        raise TypeError(f"{cls.__name__} must declare a non-empty class-level 'id: str'")
    if filter_id in FILTER_REGISTRY:
        existing = FILTER_REGISTRY[filter_id].__name__
        raise ValueError(
            f"filter id {filter_id!r} already registered by {existing}; "
            f"cannot re-register from {cls.__name__}"
        )
    FILTER_REGISTRY[filter_id] = cls
    return cls


def resolve(filter_id: str) -> type[Filter]:
    """Look up a registered filter class by id; raise ``KeyError`` if missing."""
    try:
        return FILTER_REGISTRY[filter_id]
    except KeyError as exc:
        raise KeyError(
            f"unknown filter id {filter_id!r}; known: {sorted(FILTER_REGISTRY)}"
        ) from exc


__all__ = ["FILTER_REGISTRY", "register", "resolve"]
