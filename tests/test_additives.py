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
    """Answers execute() calls in the order the caller makes them.

    resolve_additives asks for the catalog, then this cycle's feedings, then the
    farm's. current_doses asks for the catalog, then the farm's, then the cycle's.
    """

    def __init__(self, *result_sets):
        self.responses = [_Result(list(rows)) for rows in result_sets]

    async def execute(self, stmt):
        assert self.responses, "more queries than the test set up"
        return self.responses.pop(0)


VITC = SimpleNamespace(id=uuid4(), name="Vitamin C", base_unit="kg")
PROBIO = SimpleNamespace(id=uuid4(), name="Probiotic", base_unit="L")
CATALOG = [VITC, PROBIO]


async def _resolve(entries, history=(), farm=(), at=(date(2026, 9, 20), time(12, 0)), catalog=CATALOG):
    return await resolve_additives(
        _FakeDb(catalog, history, farm),
        farm_id=uuid4(),
        cycle_id=uuid4(),
        entries=[FeedingAdditive(**e) for e in entries],
        log_date=at[0],
        feed_time=at[1],
    )


def _used(product, dose, name=None):
    """A stored entry in the current shape."""
    return {"product_id": str(product.id), "name": name or product.name, "dose_per_kg": dose}


@pytest.mark.asyncio
async def test_name_matches_catalog_ignoring_case_and_keeps_decimal_dose():
    out = await _resolve([{"name": "  vitamin   c ", "dose_per_kg": "2.5"}])
    # Vitamin C is counted in kg, so its dose is grams per kg of feed.
    assert out == [{"product_id": str(VITC.id), "name": "Vitamin C", "dose_per_kg": "2.5", "dose_unit": "g"}]


@pytest.mark.asyncio
async def test_missing_dose_uses_last_dose_in_cycle_over_catalog_default():
    history = [
        (date(2026, 9, 10), time(7, 0), [_used(VITC, "2")], uuid4()),
        # Late-cycle change: carries forward to later feedings.
        (date(2026, 9, 18), time(7, 0), [_used(VITC, "3.5")], uuid4()),
        # After the feeding being logged: must not count.
        (date(2026, 9, 21), time(7, 0), [_used(VITC, "9")], uuid4()),
    ]
    out = await _resolve([{"product_id": VITC.id}], history=history)
    assert out[0]["dose_per_kg"] == "3.5"


@pytest.mark.asyncio
async def test_entries_from_before_the_catalogs_merged_are_matched_by_name():
    """Pre-merge feedings carry `additive_id` and `dosage_gr_per_kg`, not a product id."""
    history = [(date(2026, 9, 1), time(7, 0), [{"additive_id": 7, "name": "VITAMIN C", "dosage_gr_per_kg": 4}], uuid4())]
    out = await _resolve([{"name": "Vitamin C"}], history=history)
    assert out[0]["dose_per_kg"] == "4"


@pytest.mark.asyncio
async def test_a_dose_never_used_anywhere_must_be_typed_in():
    """No stored default: nothing on the farm has ever dosed it."""
    with pytest.raises(HTTPException) as err:
        await _resolve([{"name": "Vitamin C"}])
    assert err.value.status_code == 400
    assert "No dose for Vitamin C" in err.value.detail
    # Counted in kg, so it is dosed in grams per kg of feed.
    assert "(g per kg of feed)" in err.value.detail


@pytest.mark.asyncio
async def test_the_dose_unit_follows_what_the_thing_is_counted_in():
    """Probiotic is a volume, so it is dosed in mL, never in grams."""
    with pytest.raises(HTTPException) as err:
        await _resolve([{"name": "Probiotic"}])
    assert "(mL per kg of feed)" in err.value.detail


@pytest.mark.asyncio
async def test_unknown_or_duplicate_additives_are_rejected():
    with pytest.raises(HTTPException) as err:
        await _resolve([{"name": "Zeolite", "dose_per_kg": 1}])
    assert "Unknown additive 'Zeolite'" in err.value.detail
    with pytest.raises(HTTPException) as err:
        await _resolve([{"product_id": uuid4(), "dose_per_kg": 1}])
    assert "not in this farm's catalog" in err.value.detail
    with pytest.raises(HTTPException) as err:
        await _resolve([{"product_id": VITC.id, "dose_per_kg": 1}, {"name": "vitamin c", "dose_per_kg": 2}])
    assert "listed twice" in err.value.detail


def test_additive_needs_an_id_or_a_name_and_a_non_negative_dose():
    with pytest.raises(ValidationError):
        FeedingAdditive(dose_per_kg=1)
    with pytest.raises(ValidationError):
        FeedingAdditive(name="Vitamin C", dose_per_kg=-1)


def test_feeding_out_reads_both_shapes_and_fills_grams():
    feeding = FeedingOut.model_validate(
        SimpleNamespace(
            id=uuid4(),
            daily_log_id=uuid4(),
            feed_time=time(7, 0),
            amount_kg=Decimal("12.4"),
            duration_min=None,
            additives=[
                # Pre-merge: no product id, dose under the old key.
                {"name": "Lactobacillus", "dosage_gr_per_kg": 100},
                _used(VITC, "2.5"),
            ],
            feed_types=[],
            notes=None,
            updated_at=datetime.now(timezone.utc),
            updated_by=None,
            updated_by_type=None,
        )
    )
    assert feeding.additives[0].product_id is None and feeding.additives[0].amount_g == Decimal("1240.0")
    assert feeding.additives[1].product_id == VITC.id and feeding.additives[1].amount_g == Decimal("31.0")


@pytest.mark.asyncio
async def test_this_cycle_beats_what_the_farm_did_before():
    farm = [(date(2026, 8, 1), time(7, 0), [_used(PROBIO, "9"), _used(VITC, "1")], uuid4())]
    cycle = [(date(2026, 9, 5), time(7, 0), [_used(PROBIO, "5")], uuid4())]
    # current_doses asks for the farm's history first, then this cycle's.
    out = await current_doses(_FakeDb(CATALOG, farm, cycle), uuid4(), uuid4(), date(2026, 9, 22))
    by_name = {d["name"]: d for d in out}
    # The cycle has dosed Probiotic, so its own 5 wins over the farm's 9.
    assert by_name["Probiotic"]["dose_per_kg"] == Decimal("5")
    assert by_name["Probiotic"]["dose_unit"] == "mL"
    # It has never dosed Vitamin C, so the farm's last dose stands in.
    assert by_name["Vitamin C"]["dose_per_kg"] == Decimal("1")


@pytest.mark.asyncio
async def test_a_new_cycle_starts_from_what_the_farm_last_did():
    farm = [(date(2026, 8, 1), time(7, 0), [_used(VITC, "6")], uuid4())]
    out = await _resolve([{"name": "Vitamin C"}], history=(), farm=farm)
    assert out[0]["dose_per_kg"] == "6"


@pytest.mark.asyncio
async def test_usage_by_day_sums_grams_and_averages_dose():
    day = date(2026, 9, 22)
    rows = [
        (day, Decimal("10"), [_used(VITC, "2")]),
        (day, Decimal("10"), [_used(VITC, "4")]),
        (day, Decimal("5"), []),
    ]
    out = await usage_by_day(_FakeDb(CATALOG, rows), uuid4(), uuid4(), day, day)
    assert out == [
        {
            "date": day,
            "product_id": str(VITC.id),
            "name": "Vitamin C",
            "feed_kg": Decimal("20.000"),
            "amount_g": Decimal("60.0"),
            "dosage_gr_per_kg": Decimal("3.000"),
        }
    ]
