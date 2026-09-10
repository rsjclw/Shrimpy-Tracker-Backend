from app.services import login_throttle as module
from app.services.login_throttle import LoginThrottle


def _throttle(monkeypatch, start: float = 1000.0) -> tuple[LoginThrottle, list[float]]:
    clock = [start]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    return LoginThrottle(window_seconds=60, max_per_email=3, max_per_ip=5), clock


def test_email_is_blocked_after_max_failures(monkeypatch):
    throttle, _ = _throttle(monkeypatch)

    for _ in range(3):
        assert throttle.retry_after("a@x.com", "1.1.1.1") == 0
        throttle.record_failure("a@x.com", "1.1.1.1")

    assert throttle.retry_after("a@x.com", "1.1.1.1") > 0
    assert throttle.retry_after("b@x.com", "1.1.1.1") == 0


def test_failures_expire_after_window(monkeypatch):
    throttle, clock = _throttle(monkeypatch)
    for _ in range(3):
        throttle.record_failure("a@x.com", "1.1.1.1")
    assert throttle.retry_after("a@x.com", "1.1.1.1") > 0

    clock[0] += 61

    assert throttle.retry_after("a@x.com", "1.1.1.1") == 0


def test_successful_login_clears_email_counter(monkeypatch):
    throttle, _ = _throttle(monkeypatch)
    for _ in range(3):
        throttle.record_failure("a@x.com", "1.1.1.1")

    throttle.clear("a@x.com")

    assert throttle.retry_after("a@x.com", "1.1.1.1") == 0


def test_ip_limit_applies_across_emails(monkeypatch):
    throttle, _ = _throttle(monkeypatch)
    for i in range(5):
        throttle.record_failure(f"user{i}@x.com", "9.9.9.9")

    assert throttle.retry_after("fresh@x.com", "9.9.9.9") > 0
    assert throttle.retry_after("fresh@x.com", "8.8.8.8") == 0
