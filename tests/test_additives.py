from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.feeding import FeedingAdditive, FeedingOut
from app.services.additives import current_doses, resolve_additives, usage_by_day


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _FakeDb:
    """Answers execute() calls in order: the farm catalog first, then feeding history rows."""

    def __init__(self, catalog, history=()):
        self.responses = [_Result(catalog), _Result(list(history))]

    async def execute(self, stmt):
        return self.responses.pop(0)


VITC = SimpleNamespace(id=1, name="Vitamin C", dosage_gr_per_kg=Decimal("2"))
PROBIO = SimpleNamespace(id=2, name="Probiotic", dosage_gr_per_kg=None)
CATALOG = [VITC, PROBIO]


async def _resolve(entries, history=(), at=(date(2026, 9, 20), time(12, 0)), catalog=CATALOG):
    return await resolve_additives(
        _FakeDb(catalog, history),
        farm_id=uuid4(),
        cycle_id=uuid4(),
        entries=[FeedingAdditive(**e) for e in entries],
        log_date=at[0],
        feed_time=at[1],
    )


@pytest.mark.asyncio
async def test_name_matches_catalog_ignoring_case_and_keeps_decimal_dose():
    out = await _resolve([{"name": "  vitamin   c ", "dosage_gr_per_kg": "2.5"}])
    assert out == [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "2.5"}]


@pytest.mark.asyncio
async def test_missing_dose_uses_last_dose_in_cycle_over_catalog_default():
    history = [
        (date(2026, 9, 10), time(7, 0), [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "2"}], uuid4()),
        # Late-cycle change: carries forward to later feedings.
        (date(2026, 9, 18), time(7, 0), [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "3.5"}], uuid4()),
        # After the feeding being logged: must not count.
        (date(2026, 9, 21), time(7, 0), [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "9"}], uuid4()),
    ]
    out = await _resolve([{"additive_id": 1}], history=history)
    assert out[0]["dosage_gr_per_kg"] == "3.5"


@pytest.mark.asyncio
async def test_legacy_history_entries_without_id_are_matched_by_name():
    history = [(date(2026, 9, 1), time(7, 0), [{"name": "VITAMIN C", "dosage_gr_per_kg": 4}], uuid4())]
    out = await _resolve([{"name": "Vitamin C"}], history=history)
    assert out[0]["dosage_gr_per_kg"] == "4"


@pytest.mark.asyncio
async def test_missing_dose_without_history_uses_catalog_default():
    out = await _resolve([{"name": "Vitamin C"}])
    assert out[0]["dosage_gr_per_kg"] == "2"


@pytest.mark.asyncio
async def test_missing_dose_without_history_or_default_is_rejected():
    with pytest.raises(HTTPException) as err:
        await _resolve([{"name": "Probiotic"}])
    assert err.value.status_code == 400 and "No dose for Probiotic" in err.value.detail


@pytest.mark.asyncio
async def test_unknown_or_duplicate_additives_are_rejected():
    with pytest.raises(HTTPException) as err:
        await _resolve([{"name": "Zeolite", "dosage_gr_per_kg": 1}])
    assert "Unknown additive 'Zeolite'" in err.value.detail
    with pytest.raises(HTTPException) as err:
        await _resolve([{"additive_id": 99, "dosage_gr_per_kg": 1}])
    assert "not in this farm" in err.value.detail
    with pytest.raises(HTTPException) as err:
        await _resolve([{"additive_id": 1, "dosage_gr_per_kg": 1}, {"name": "vitamin c", "dosage_gr_per_kg": 2}])
    assert "listed twice" in err.value.detail


def test_additive_needs_an_id_or_a_name_and_a_non_negative_dose():
    with pytest.raises(ValidationError):
        FeedingAdditive(dosage_gr_per_kg=1)
    with pytest.raises(ValidationError):
        FeedingAdditive(name="Vitamin C", dosage_gr_per_kg=-1)


def test_feeding_out_reads_legacy_entries_and_fills_grams():
    feeding = FeedingOut.model_validate(
        SimpleNamespace(
            id=uuid4(),
            daily_log_id=uuid4(),
            feed_time=time(7, 0),
            amount_kg=Decimal("12.4"),
            duration_min=None,
            additives=[{"name": "Lactobacillus", "dosage_gr_per_kg": 100}, {"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "2.5"}],
            feed_types=[],
            notes=None,
            updated_at=datetime.now(timezone.utc),
            updated_by=None,
            updated_by_type=None,
        )
    )
    assert feeding.additives[0].additive_id is None and feeding.additives[0].amount_g == Decimal("1240.0")
    assert feeding.additives[1].amount_g == Decimal("31.0")


@pytest.mark.asyncio
async def test_current_doses_reports_source():
    history = [(date(2026, 9, 5), time(7, 0), [{"additive_id": 2, "name": "Probiotic", "dosage_gr_per_kg": "5"}], uuid4())]
    out = await current_doses(_FakeDb(CATALOG, history), uuid4(), uuid4(), date(2026, 9, 22))
    by_name = {d["name"]: d for d in out}
    assert by_name["Probiotic"]["source"] == "last_used" and by_name["Probiotic"]["dosage_gr_per_kg"] == Decimal("5")
    assert by_name["Vitamin C"]["source"] == "default" and by_name["Vitamin C"]["dosage_gr_per_kg"] == Decimal("2")


@pytest.mark.asyncio
async def test_usage_by_day_sums_grams_and_averages_dose():
    day = date(2026, 9, 22)
    rows = [
        (day, Decimal("10"), [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "2"}]),
        (day, Decimal("10"), [{"additive_id": 1, "name": "Vitamin C", "dosage_gr_per_kg": "4"}]),
        (day, Decimal("5"), []),
    ]
    out = await usage_by_day(_FakeDb(CATALOG, rows), uuid4(), uuid4(), day, day)
    assert out == [
        {"date": day, "additive_id": 1, "name": "Vitamin C", "feed_kg": Decimal("20.000"), "amount_g": Decimal("60.0"), "dosage_gr_per_kg": Decimal("3.000")}
    ]
