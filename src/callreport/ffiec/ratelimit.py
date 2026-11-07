"""A thread-safe limiter that caps requests per rolling hour."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW = 3600.0


class RateLimiter:
    """Allow at most ``requests_per_hour`` request starts in any rolling hour.

    FFIEC allows roughly 2,500 requests per hour. Requests run at full speed
    until the hourly budget is used up; only then does a caller wait, until the
    oldest request in the window is an hour old.
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
        self.limit = max(1, int(requests_per_hour))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._starts: deque[float] = deque()

    def acquire(self) -> None:
        """Block until the caller may start its request."""
        with self._lock:
            now = self._clock()
            while self._starts and self._starts[0] <= now - WINDOW:
                self._starts.popleft()
            # At the cap, reserve the slot freed when the oldest start leaves the window.
            start = now if len(self._starts) < self.limit else self._starts.popleft() + WINDOW
            self._starts.append(start)
        if start > now:
            self._sleep(start - now)
