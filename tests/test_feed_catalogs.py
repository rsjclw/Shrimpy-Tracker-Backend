from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas import AdditiveCreate, AdditiveOut, FeedTypeCreate, FeedTypeOut
from app.services.day_view import _default_feed_types


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class _ExecuteResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _ScalarResult(self.values)

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class _FakeDb:
    def __init__(self, *responses):
        self.responses = list(responses)

    async def execute(self, stmt):
        return self.responses.pop(0)


def test_catalog_schemas_include_persistent_ids():
    farm_id = uuid4()
    feed_type_id = uuid4()
    feed_type = FeedTypeOut.model_validate(
        SimpleNamespace(
            id=feed_type_id,
            farm_id=farm_id,
            brand="Brand A",
            type="Starter",
            price_per_kg=Decimal("18000"),
            notes=None,
            created_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        )
    )
    additive = AdditiveOut.model_validate(
        SimpleNamespace(id=1, farm_id=farm_id, name="Vitagold", dosage_gr_per_kg=Decimal("3.000"))
    )

    assert FeedTypeCreate(farm_id=farm_id, brand="Brand A", type="Starter", price_per_kg=Decimal("18000"))
    assert feed_type.id == feed_type_id
    assert AdditiveCreate(farm_id=farm_id, name="Vitagold", dosage_gr_per_kg=Decimal("3.000"))
    assert additive.id == 1


@pytest.mark.asyncio
async def test_default_feed_types_use_latest_previous_feeding_mix():
    previous_mix = [
        {
            "feed_type_id": "feed-a",
            "brand": "Brand A",
            "type": "Starter",
            "price_per_kg": "18000",
            "percentage": "100",
            "notes": None,
        }
    ]
    db = _FakeDb(_ExecuteResult([previous_mix]))

    result = await _default_feed_types(db, SimpleNamespace(id=uuid4()), datetime(2026, 5, 5).date())

    assert len(result) == 1
    assert result[0].feed_type_id == "feed-a"
    assert result[0].percentage == Decimal("100")
    assert db.responses == []


@pytest.mark.asyncio
async def test_default_feed_types_fall_back_to_first_farm_feed_type():
    feed_type_id = uuid4()
    farm_id = uuid4()
    pond_id = uuid4()
    db = _FakeDb(
        _ExecuteResult([]),
        _ExecuteResult([farm_id]),
        _ExecuteResult(
            [
                SimpleNamespace(
                    id=feed_type_id,
                    brand="Brand B",
                    type="Grower",
                    price_per_kg=Decimal("17000"),
                    notes="Main feed",
                )
            ]
        ),
    )

    result = await _default_feed_types(db, SimpleNamespace(id=uuid4(), pond_id=pond_id), datetime(2026, 5, 5).date())

    assert len(result) == 1
    assert result[0].feed_type_id == str(feed_type_id)
    assert result[0].brand == "Brand B"
    assert result[0].percentage == Decimal("100")


@pytest.mark.asyncio
async def test_default_feed_types_are_empty_without_history_or_catalog():
    db = _FakeDb(_ExecuteResult([]), _ExecuteResult([uuid4()]), _ExecuteResult([]))

    result = await _default_feed_types(db, SimpleNamespace(id=uuid4(), pond_id=uuid4()), datetime(2026, 5, 5).date())

    assert result == []
