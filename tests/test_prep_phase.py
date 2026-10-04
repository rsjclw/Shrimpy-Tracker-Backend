from datetime import date, time
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import CycleCreate
from app.services import metrics as M
from app.services.day_view import _compute_metrics

POND = "11111111-1111-1111-1111-111111111111"


def _create(**kw):
    return CycleCreate(pond_id=POND, name="4", **kw)


def test_a_preparing_cycle_needs_its_prep_start_and_nothing_about_shrimp():
    c = _create(status="preparing", prep_start_date=date(2026, 10, 1), start_date=date(2026, 10, 15))
    assert c.initial_population is None and c.initial_abw_g is None
    with pytest.raises(ValidationError):
        _create(status="preparing", start_date=date(2026, 10, 15))  # no prep_start_date
    with pytest.raises(ValidationError):
        _create(status="preparing", prep_start_date=date(2026, 10, 1), start_date=date(2026, 9, 30))  # stocking before prep
    with pytest.raises(ValidationError):
        _create(status="preparing", prep_start_date=date(2026, 10, 1), start_date=date(2026, 10, 15), initial_population=100_000)


def test_stocking_now_still_needs_population_and_abw():
    assert _create(start_date=date(2026, 10, 1), initial_population=100_000, initial_abw_g=Decimal("0.001")).status == "active"
    with pytest.raises(ValidationError):
        _create(start_date=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        _create(start_date=date(2026, 10, 1), initial_population=100_000, initial_abw_g=Decimal("0.001"), prep_start_date=date(2026, 9, 20))
    with pytest.raises(ValidationError):
        _create(status="crashed", start_date=date(2026, 10, 1), initial_population=1, initial_abw_g=Decimal("0"))


def _cycle(**kw):
    base = dict(start_date=date(2026, 10, 15), prep_start_date=date(2026, 10, 1), initial_population=None, initial_abw_g=None,
                status="preparing", actual_end_date=None, planned_end_date=None)
    return SimpleNamespace(**{**base, **kw})


def test_a_preparing_cycle_has_no_shrimp_metrics():
    m = _compute_metrics(_cycle(), date(2026, 10, 5), [], [], [], [])
    assert m.doc == -9
    assert m.estimated_population is None and m.estimated_biomass_kg is None and m.fcr is None


def test_after_stocking_the_preparation_days_still_have_no_shrimp():
    stocked = _cycle(status="active", initial_population=100_000, initial_abw_g=Decimal("0.001"))
    feeds = [M.FeedingRow(date=date(2026, 10, 15), amount_kg=Decimal("10"), feed_time=time(7, 0))]
    assert _compute_metrics(stocked, date(2026, 10, 10), feeds, [], [], []).estimated_population is None
    assert _compute_metrics(stocked, date(2026, 10, 15), feeds, [], [], []).estimated_population == 100_000


def test_a_preparing_cycle_is_running_not_ended():
    assert M.cycle_end_date(_cycle(), date(2026, 10, 4)) is None
    assert M.cycle_end_date(_cycle(status="cancelled", actual_end_date=date(2026, 10, 3)), date(2026, 10, 4)) == date(2026, 10, 3)
