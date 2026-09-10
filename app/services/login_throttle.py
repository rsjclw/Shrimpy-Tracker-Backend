import time
from collections import deque
from threading import Lock

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 10
# Requests arrive through the Vercel proxy, so many users can share one IP -
# keep this generous and rely on the per-email limit to stop guessing.
MAX_FAILURES_PER_IP = 100


class LoginThrottle:
    """Sliding-window count of failed sign-ins, in memory (single uvicorn process)."""

    def __init__(
        self,
        window_seconds: int = WINDOW_SECONDS,
        max_per_email: int = MAX_FAILURES_PER_EMAIL,
        max_per_ip: int = MAX_FAILURES_PER_IP,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_per_email = max_per_email
        self.max_per_ip = max_per_ip
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        window = self._failures.get(key)
        if window is None:
            return deque()
        while window and window[0] <= now - self.window_seconds:
            window.popleft()
        if not window:
            del self._failures[key]
        return window

    def retry_after(self, email: str, ip: str) -> int:
        """Seconds until another attempt is allowed; 0 when not throttled."""
        now = time.monotonic()
        with self._lock:
            for key, limit in ((f"email:{email}", self.max_per_email), (f"ip:{ip}", self.max_per_ip)):
                window = self._recent(key, now)
                if len(window) >= limit:
                    return max(1, int(window[0] + self.window_seconds - now) + 1)
        return 0

    def record_failure(self, email: str, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (f"email:{email}", f"ip:{ip}"):
                self._failures.setdefault(key, deque()).append(now)

    def clear(self, email: str) -> None:
        with self._lock:
            self._failures.pop(f"email:{email}", None)


login_throttle = LoginThrottle()
