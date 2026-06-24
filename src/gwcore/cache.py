"""In-process TTL cache for warm-Lambda reuse.

A module-scoped cache that survives across invocations on a warm Lambda and
evaporates on cold start — no external dependency, no per-request network cost.
Used for hot, slowly-changing config (pricing table, routing configs, tier
defaults) and for the JWKS key set (see ``gwcore.auth``).

This is deliberately NOT the LLM response cache — that stays at Redis (ADR-012).

Time is injected via a ``clock`` callable so tests are deterministic; it
defaults to ``time.monotonic`` (immune to wall-clock jumps).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_Clock = Callable[[], float]


@dataclass
class _Entry[T]:
    value: T
    expires_at: float


class TTLCache[T]:
    """Thread-safe in-process cache with per-key TTL.

    Lambda is single-threaded per invocation, but provisioned-concurrency and
    extension threads can touch a module-scoped cache concurrently, so the
    lock is cheap insurance.
    """

    def __init__(self, *, default_ttl: float = 300.0, clock: _Clock = time.monotonic) -> None:
        self._default_ttl = default_ttl
        self._clock = clock
        self._store: dict[str, _Entry[T]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> T | None:
        """Return the cached value if present and unexpired, else ``None``."""
        now = self._clock()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: T, *, ttl: float | None = None) -> None:
        """Cache ``value`` under ``key`` for ``ttl`` seconds (or the default)."""
        expires_at = self._clock() + (self._default_ttl if ttl is None else ttl)
        with self._lock:
            self._store[key] = _Entry(value=value, expires_at=expires_at)

    def invalidate(self, key: str) -> None:
        """Drop a single key (no error if absent)."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Drop everything."""
        with self._lock:
            self._store.clear()

    def read_through(self, key: str, loader: Callable[[], T], *, ttl: float | None = None) -> T:
        """Return the cached value, or call ``loader`` once, cache, and return it.

        The loader runs only on a miss. If the loader raises, nothing is cached
        and the exception propagates (callers map it to an ``UpstreamError``).
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        value = loader()
        self.set(key, value, ttl=ttl)
        return value
