import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Product, ProductComponent, ProductUnit
from app.services.feed_stock import (
    Taken,
    carried_mixes,
    counts,
    differences,
    goal,
    settled_by_count,
    targets,
    entry_product,
    first_feed,
    unchanged,
    wanted,
)
from app.services.products import Catalog, ProductError

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _product(name: str, base_unit: str = "kg", category: str = "feed", kind: str = "product", **extra) -> Product:
    return Product(
        id=uuid.uuid4(),
        farm_id=uuid.uuid4(),
        name=name,
        category=category,
        kind=kind,
        base_unit=base_unit,
        tracked=extra.pop("tracked", True),
        active=extra.pop("active", True),
        created_at=extra.pop("created_at", _T0),
        **extra,
    )


def _catalog(products, units=(), components=()) -> tuple[Catalog, dict]:
    catalog = Catalog(list(products), list(units), list(components))
    return catalog, {" ".join(p.name.split()).lower(): p for p in products}


def _feed(product: Product, percentage) -> dict:
    return {"product_id": str(product.id), "name": product.name, "percentage": percentage}


# --- which feed each feeding was fed ----------------------------------------


def test_a_feeding_without_a_mix_follows_the_one_before_it():
    c2, d3 = [{"product_id": "c2", "percentage": 100}], [{"product_id": "d3", "percentage": 100}]
    assert carried_mixes([c2, [], [], d3, []], fallback=[]) == [c2, c2, c2, d3, d3]


def test_feedings_before_any_mix_take_the_fallback():
    c2 = [{"product_id": "c2", "percentage": 100}]
    fallback = [{"product_id": "first", "percentage": 100}]
    assert carried_mixes([[], c2, []], fallback) == [fallback, c2, c2]


def test_the_farm_fallback_is_its_first_active_feed():
    old = _product("Old feed", created_at=_T0, active=False)
    first = _product("Haida HDV-C", created_at=_T0 + timedelta(days=1))
    later = _product("Haida HDV-D", created_at=_T0 + timedelta(days=2))
    lime = _product("Dolomite", category="lime_minerals", created_at=_T0)
    catalog, _ = _catalog([later, lime, old, first])
    assert first_feed(catalog) == [{"product_id": str(first.id), "name": "Haida HDV-C", "percentage": 100}]
    assert first_feed(_catalog([lime])[0]) == []


# --- which catalog entry a stored line means --------------------------------


def test_lines_are_matched_by_id_then_legacy_id_then_name():
    c2l = _product("Haida HDV-C2L")
    catalog, by_name = _catalog([c2l])
    assert entry_product(catalog, by_name, {"product_id": str(c2l.id)}) is c2l
    assert entry_product(catalog, by_name, {"feed_type_id": str(c2l.id), "brand": "x"}) is c2l
    # The prediction engine once wrote ids that are not UUIDs.
    assert entry_product(catalog, by_name, {"product_id": "pred-1", "name": "haida  hdv-c2l"}) is c2l
    assert entry_product(catalog, by_name, {"brand": "Haida", "type": "HDV-C2L"}) is c2l
    assert entry_product(catalog, by_name, {"product_id": str(uuid.uuid4()), "name": "Gone"}) is None


# --- what one feeding takes -------------------------------------------------


def test_the_amount_is_split_by_the_mix():
    c2l, d3 = _product("C2L"), _product("D3")
    catalog, by_name = _catalog([c2l, d3])
    taken = wanted(catalog, by_name, Decimal("22"), [_feed(c2l, 50), _feed(d3, 50)], [])
    assert taken == {c2l.id: Decimal("11"), d3.id: Decimal("11")}


def test_uneven_shares_round_to_grams():
    c1, c2 = _product("C1"), _product("C2")
    catalog, by_name = _catalog([c1, c2])
    taken = wanted(catalog, by_name, Decimal("10"), [_feed(c1, Decimal("33.33")), _feed(c2, Decimal("66.67"))], [])
    assert taken == {c1.id: Decimal("3.333"), c2.id: Decimal("6.667")}


def test_additive_doses_convert_to_the_products_own_unit():
    """2 g/kg of something counted in kg over 20 kg of feed is 0.04 kg off the shelf, not 40."""
    feed = _product("C2")
    vitagold = _product("Vitagold", base_unit="kg", category="supplements")
    stimuno = _product("Stimuno", base_unit="g", category="supplements")
    biolacto = _product("Biolacto", base_unit="L", category="probiotics")
    catalog, by_name = _catalog([feed, vitagold, stimuno, biolacto])
    additives = [
        {"product_id": str(vitagold.id), "name": "Vitagold", "dose_per_kg": "2", "dose_unit": "g"},
        {"product_id": str(stimuno.id), "name": "Stimuno", "dose_per_kg": "2", "dose_unit": "g"},
        {"product_id": str(biolacto.id), "name": "Biolacto", "dose_per_kg": "100", "dose_unit": "mL"},
    ]
    taken = wanted(catalog, by_name, Decimal("20"), [_feed(feed, 100)], additives)
    assert taken == {
        feed.id: Decimal("20"),
        vitagold.id: Decimal("0.04"),
        stimuno.id: Decimal("40"),
        biolacto.id: Decimal("2"),
    }


def test_legacy_dose_keys_still_count():
    feed, ab = _product("C2"), _product("AB", base_unit="g", category="supplements")
    catalog, by_name = _catalog([feed, ab])
    taken = wanted(catalog, by_name, Decimal("5"), [_feed(feed, 100)], [{"name": "AB", "dosage_gr_per_kg": 1}])
    assert taken[ab.id] == Decimal("5")


def test_a_formula_expands_and_untracked_parts_drop_out():
    biolacto = _product("Biolacto", base_unit="L", category="probiotics")
    water = _product("Water", base_unit="L", category="other", tracked=False)
    lacto = _product("Lactobacillus", base_unit="L", category="probiotics", kind="formula")
    components = [
        ProductComponent(id=uuid.uuid4(), product_id=lacto.id, component_product_id=biolacto.id, quantity=Decimal("0.25")),
        ProductComponent(id=uuid.uuid4(), product_id=lacto.id, component_product_id=water.id, quantity=Decimal("0.75")),
    ]
    feed = _product("C2")
    catalog, by_name = _catalog([feed, biolacto, water, lacto], components=components)
    additives = [{"product_id": str(lacto.id), "name": "Lactobacillus", "dose_per_kg": "200", "dose_unit": "mL"}]
    taken = wanted(catalog, by_name, Decimal("10"), [_feed(feed, 100)], additives)
    # 200 mL/kg x 10 kg = 2 L of Lactobacillus, a quarter of it Biolacto.
    assert taken == {feed.id: Decimal("10"), biolacto.id: Decimal("0.5")}


def test_a_formula_with_no_recipe_yet_takes_nothing():
    lacto = _product("Lactobacillus", base_unit="L", category="probiotics", kind="formula")
    feed = _product("C2")
    catalog, by_name = _catalog([feed, lacto])
    additives = [{"product_id": str(lacto.id), "name": "Lactobacillus", "dose_per_kg": "200", "dose_unit": "mL"}]
    assert wanted(catalog, by_name, Decimal("10"), [_feed(feed, 100)], additives) == {feed.id: Decimal("10")}


def test_lines_with_no_stock_to_come_out_of_take_nothing():
    feed = _product("C2")
    sacks_only = _product("Odd feed", base_unit="pcs")
    liquid = _product("Liquid", base_unit="L", category="supplements")
    catalog, by_name = _catalog([feed, sacks_only, liquid])
    mix = [_feed(feed, 50), _feed(sacks_only, 30), {"product_id": str(uuid.uuid4()), "name": "Gone", "percentage": 20}]
    # A grams dose on a liquid is from before the dose unit followed the product.
    additives = [{"product_id": str(liquid.id), "name": "Liquid", "dose_per_kg": "1", "dose_unit": "g"}]
    assert wanted(catalog, by_name, Decimal("10"), mix, additives) == {feed.id: Decimal("5")}


def test_a_product_counted_in_pieces_converts_through_its_kg_unit():
    feed = _product("Sack feed", base_unit="pcs")
    catalog, by_name = _catalog(
        [feed], units=[ProductUnit(id=uuid.uuid4(), product_id=feed.id, unit="kg", factor_to_base=Decimal("0.04"))]
    )
    assert wanted(catalog, by_name, Decimal("50"), [_feed(feed, 100)], []) == {feed.id: Decimal("2")}


def test_a_zero_feeding_takes_nothing():
    feed = _product("C2")
    catalog, by_name = _catalog([feed])
    assert wanted(catalog, by_name, Decimal("0"), [_feed(feed, 100)], []) == {}


def test_a_recipe_that_contains_itself_is_refused():
    loop = _product("Loop", kind="formula")
    feed = _product("C2")
    components = [ProductComponent(id=uuid.uuid4(), product_id=loop.id, component_product_id=loop.id, quantity=Decimal("1"))]
    catalog, by_name = _catalog([feed, loop], components=components)
    with pytest.raises(ProductError):
        wanted(catalog, by_name, Decimal("1"), [_feed(loop, 100)], [])


# --- settling against the ledger --------------------------------------------


def test_a_feeding_with_what_it_should_have_out_is_left_alone_wherever_it_came_from():
    c2 = uuid.uuid4()
    old_warehouse_row = Taken(uuid.uuid4(), c2, Decimal("10"))
    assert unchanged([old_warehouse_row], {c2: Decimal("10.000")})
    assert not unchanged([old_warehouse_row], {c2: Decimal("8")})
    assert not unchanged([], {c2: Decimal("8")})
    assert unchanged([], {})


def test_differences_take_only_what_changed():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # 10 kg of a becomes 8; b is new; c was in another warehouse and goes back.
    taken = {a: Decimal("10"), c: Decimal("3")}
    target = {a: Decimal("8"), b: Decimal("2")}
    assert differences(taken, target) == {a: Decimal("-2"), b: Decimal("2"), c: Decimal("-3")}
    assert differences(target, target) == {}


# --- when a feeding starts taking stock -------------------------------------

_START = date(2026, 10, 11)


def test_a_planned_feeding_takes_stock_only_once_its_time_comes():
    """Early on the 12th the day's 10:00 feed is still a plan, so its feed is still in the warehouse."""
    morning = datetime(2026, 10, 12, 6, 24)
    assert counts(date(2026, 10, 12), time(6, 0), _START, None, morning)
    assert not counts(date(2026, 10, 12), time(10, 0), _START, None, morning)
    assert counts(date(2026, 10, 12), time(10, 0), _START, None, datetime(2026, 10, 12, 10, 0))
    assert counts(date(2026, 10, 11), time(18, 0), _START, None, morning)
    assert not counts(date(2026, 10, 13), time(6, 0), _START, None, morning)


def test_nothing_before_the_start_or_after_the_cycle_ended_takes_stock():
    later = datetime(2026, 12, 1, 12, 0)
    assert not counts(date(2026, 10, 10), time(6, 0), _START, None, later)
    assert counts(date(2026, 11, 1), time(6, 0), _START, date(2026, 11, 1), later)
    assert not counts(date(2026, 11, 2), time(6, 0), _START, date(2026, 11, 1), later)


# --- stock counts -------------------------------------------------------------

_WIB = timezone(timedelta(hours=7))


def test_a_count_after_a_feed_settles_it_for_good():
    """Counted at 06:23: the 06:00 feed is in the count, so editing it later moves nothing."""
    c2l, row = uuid.uuid4(), uuid.uuid4()
    counted_at = {row: datetime(2026, 10, 12, 6, 23, tzinfo=_WIB)}
    six = datetime(2026, 10, 12, 6, 0, tzinfo=_WIB)
    ten = datetime(2026, 10, 12, 10, 0, tzinfo=_WIB)
    assert settled_by_count([], {c2l: Decimal("20")}, six, counted_at, {c2l: row}) == {c2l}
    assert settled_by_count([], {c2l: Decimal("22.1")}, ten, counted_at, {c2l: row}) == set()
    assert settled_by_count([], {c2l: Decimal("20")}, six, {}, {c2l: row}) == set()


def test_a_held_product_keeps_what_it_has_while_the_rest_settle():
    c2l, ab, c2l_row, ab_row = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # Since the count it has nothing out of C2L; AB was never counted.
    have = [Taken(ab_row, ab, Decimal("0.019"))]
    want = {c2l: Decimal("20"), ab: Decimal("0.020")}
    assert goal(have, want, {c2l}) == {ab: Decimal("0.020")}
    assert targets(have, want, {c2l}, {ab: ab_row}) == {ab_row: Decimal("0.020")}


def test_a_feed_taken_early_and_then_counted_is_taken_again_at_its_time():
    """The 10:00 feed went out at midnight by mistake, then C2L was counted at 06:23. Its
    midnight take is inside the count (so not in `have`), and at 10:00 it is taken for real."""
    c2l, row = uuid.uuid4(), uuid.uuid4()
    want = {c2l: Decimal("22.1")}
    assert goal([], want, set()) == want
    assert targets([], want, set(), {c2l: row}) == {row: Decimal("22.1")}
    assert goal([], {}, set()) == {}  # before 10:00 it wants nothing and has nothing: no change


def test_a_pond_moved_to_another_warehouse_settles_there_once_it_changes():
    c2l, old_row, new_row = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    have = [Taken(old_row, c2l, Decimal("10"))]
    assert targets(have, {c2l: Decimal("12")}, set(), {c2l: new_row}) == {new_row: Decimal("12")}
    assert differences({old_row: Decimal("10")}, {new_row: Decimal("12")}) == {
        old_row: Decimal("-10"),
        new_row: Decimal("12"),
    }
