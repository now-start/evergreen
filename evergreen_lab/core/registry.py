from __future__ import annotations

from typing import Callable

from evergreen_lab.core.strategy import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(name: str) -> Callable[[type[Strategy]], type[Strategy]]:
    """Class decorator: register a strategy under ``name`` (case-insensitive)."""
    key = name.strip().lower()
    if not key:
        raise ValueError("strategy name must be non-empty")

    def decorator(cls: type[Strategy]) -> type[Strategy]:
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not cls:
            raise ValueError(f"strategy already registered: {key}")
        cls.name = key
        _REGISTRY[key] = cls
        return cls

    return decorator


def create(name: str, **params) -> Strategy:
    key = name.strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"unknown strategy: {name!r}. registered: {list_strategies()}")
    return _REGISTRY[key](**params)


def list_strategies() -> list[str]:
    return sorted(_REGISTRY)
