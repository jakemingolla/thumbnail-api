"""Lazy Config + boto3 client cache for Lambda ``handler()`` wrappers.

Warm LocalStack / Lambda containers keep the process alive
(``LAMBDA_KEEPALIVE_MS``). Caching here avoids rebuilding settings and clients
on every invoke. ``handle_*`` functions stay injection-based and uncached.

Do not cache ``get_config()`` globally — pytest env would leak. Tests that
monkeypatch handler-module factories must call ``reset_handler_runtime()``
(or use the autouse fixtures in the handler unit tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class RuntimeCache[T]:
    """Process-wide lazy slot for one handler module's runtime deps."""

    def __init__(self) -> None:
        """Create an empty cache."""
        self._value: T | None = None

    def get(self, factory: Callable[[], T]) -> T:
        """Return the cached value, calling ``factory`` on first use."""
        if self._value is None:
            self._value = factory()
        return self._value

    def reset(self) -> None:
        """Drop the cached value so the next ``get`` reloads."""
        self._value = None
