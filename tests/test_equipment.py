from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import EquipmentCreate, EventIn, LinkIn, PlacementIn
from app.services.equipment import current_placement, open_links, status_from_events

GRID = "11111111-1111-1111-1111-111111111111"
POND = "22222222-2222-2222-2222-222222222222"
RES = "33333333-3333-3333-3333-333333333333"
D = date(2026, 10, 1)


def test_kinds_are_a_fixed_list_and_codes_are_trimmed():
    eq = EquipmentCreate(grid_id=GRID, kind="paddlewheel", code="  A0-K1 ", specs={"hp": 1})
    assert (eq.code, eq.specs) == ("A0-K1", {"hp": 1})
    with pytest.raises(ValidationError):
        EquipmentCreate(grid_id=GRID, kind="blender", code="X1")
    with pytest.raises(ValidationError):
        EquipmentCreate(grid_id=GRID, kind="pump", code="   ")


def test_a_placement_is_in_at_most_one_thing():
    assert PlacementIn(pond_id=POND, x_m=Decimal("3.5"), y_m=Decimal("12"), bearing=90, from_date=D).bearing == 90
    assert PlacementIn(from_date=D).pond_id is None  # on the grid itself
    with pytest.raises(ValidationError):
        PlacementIn(pond_id=POND, in_equipment_id=RES, from_date=D)


def test_bearing_is_a_whole_degree_below_360_and_dates_run_forward():
    with pytest.raises(ValidationError):
        PlacementIn(pond_id=POND, bearing=360, from_date=D)
    with pytest.raises(ValidationError):
        PlacementIn(pond_id=POND, from_date=D, to_date=date(2026, 9, 1))


def test_a_link_goes_to_exactly_one_thing_from_at_most_one():
    assert LinkIn(to_pond_id=POND).from_pond_id is None  # a generator: from nothing
    assert LinkIn(from_equipment_id=RES, to_pond_id=POND).to_pond_id is not None
    with pytest.raises(ValidationError):
        LinkIn(from_pond_id=POND)
    with pytest.raises(ValidationError):
        LinkIn(to_pond_id=POND, to_equipment_id=RES)
    with pytest.raises(ValidationError):
        LinkIn(from_pond_id=POND, from_equipment_id=RES, to_pond_id=POND)


def test_event_kinds_include_disinfection_and_cost_is_not_negative():
    assert EventIn(date=D, kind="disinfected", note="Kaporit 5 kg").kind == "disinfected"
    with pytest.raises(ValidationError):
        EventIn(date=D, kind="exploded")
    with pytest.raises(ValidationError):
        EventIn(date=D, kind="serviced", cost=Decimal("-1"))


def _ev(day, kind, entered=0):
    return SimpleNamespace(date=date(2026, 10, day), kind=kind, created_at=datetime(2026, 10, 11, 0, 0, entered, tzinfo=timezone.utc))


def test_status_follows_the_latest_status_event():
    assert status_from_events([]) == "working"
    assert status_from_events([_ev(9, "broken"), _ev(10, "serviced")]) == "broken"  # servicing doesn't fix it
    assert status_from_events([_ev(9, "broken"), _ev(10, "sent_for_repair")]) == "in_repair"
    assert status_from_events([_ev(12, "repaired"), _ev(9, "broken")]) == "working"  # by date, not list order
    assert status_from_events([_ev(9, "repaired", 2), _ev(9, "broken", 1)]) == "working"  # same day: entry order
    assert status_from_events([_ev(1, "retired"), _ev(2, "note")]) == "retired"


def test_current_placement_and_open_links():
    old = SimpleNamespace(from_date=date(2026, 3, 2), to_date=date(2026, 6, 20))
    now = SimpleNamespace(from_date=date(2026, 7, 1), to_date=None)
    assert current_placement([old, now]) is now
    assert current_placement([old]) is None
    ended = SimpleNamespace(to_date=date(2026, 9, 1))
    live = SimpleNamespace(to_date=None)
    assert open_links([ended, live]) == [live]
