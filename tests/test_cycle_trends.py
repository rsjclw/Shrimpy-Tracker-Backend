from datetime import date
from types import SimpleNamespace

from app.routers.cycles import _closed_cycle_end_date


TODAY = date(2026, 8, 14)


def _cycle(*, status="active", actual_end_date=None, planned_end_date=None):
    return SimpleNamespace(
        status=status,
        actual_end_date=actual_end_date,
        planned_end_date=planned_end_date,
    )


def test_active_cycle_has_no_implicit_end_date():
    assert _closed_cycle_end_date(_cycle(), TODAY) is None


def test_closed_cycle_uses_actual_end_date():
    end = date(2026, 7, 20)

    assert (
        _closed_cycle_end_date(
            _cycle(
                status="completed",
                actual_end_date=end,
                planned_end_date=date(2026, 8, 1),
            ),
            TODAY,
        )
        == end
    )


def test_older_closed_cycle_falls_back_to_planned_end_date():
    planned = date(2026, 7, 31)

    assert (
        _closed_cycle_end_date(
            _cycle(status="completed", planned_end_date=planned),
            TODAY,
        )
        == planned
    )


def test_closed_cycle_never_extends_into_the_future():
    assert (
        _closed_cycle_end_date(
            _cycle(status="crashed", planned_end_date=date(2026, 9, 1)),
            TODAY,
        )
        == TODAY
    )
