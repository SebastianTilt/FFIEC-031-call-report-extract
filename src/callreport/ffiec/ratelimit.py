"""A thread-safe limiter that spaces out request start times."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Allow at most ``requests_per_hour``, evenly spaced, across all threads.

    FFIEC allows roughly 2,500 requests per hour and suggests spacing requests
    1.5 seconds apart (SIS611, section 3.1), which is the default here.
    """

    def __init__(
        self,
        requests_per_hour: float = 2400,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_hour <= 0:
            raise ValueError("requests_per_hour must be positive")
        self.interval = 3600.0 / requests_per_hour
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_start = 0.0

    def acquire(self) -> None:
        """Block until the caller may start its request."""
        with self._lock:
            now = self._clock()
            start = max(now, self._next_start)
            self._next_start = start + self.interval
        if start > now:
            self._sleep(start - now)
