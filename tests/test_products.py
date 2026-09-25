import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Product, ProductComponent, ProductUnit
from app.schemas import TreatmentCreate
from app.services.products import MAX_DEPTH, Catalog, ProductError, check_recipe, cost_of, expand


def _product(
    name: str, base_unit: str, kind: str = "product", tracked: bool = True, price: str | None = None
) -> Product:
    return Product(
        id=uuid.uuid4(),
        farm_id=uuid.uuid4(),
        name=name,
        category="other",
        kind=kind,
        base_unit=base_unit,
        price_per_unit=Decimal(price) if price is not None else None,
        tracked=tracked,
        active=True,
    )


def _catalog(products, units=(), components=()) -> Catalog:
    return Catalog(list(products), list(units), list(components))


def _unit(product: Product, unit: str, factor: str) -> ProductUnit:
    return ProductUnit(id=uuid.uuid4(), product_id=product.id, unit=unit, factor_to_base=Decimal(factor))


def _component(parent: Product, child: Product, quantity: str) -> ProductComponent:
    return ProductComponent(
        id=uuid.uuid4(), product_id=parent.id, component_product_id=child.id, quantity=Decimal(quantity)
    )


# --- unit conversion -------------------------------------------------------


def test_base_unit_needs_no_conversion_and_is_matched_case_insensitively():
    dolomite = _product("Dolomite", "kg")
    catalog = _catalog([dolomite])
    assert catalog.to_base(dolomite, Decimal("15"), None) == Decimal("15")
    assert catalog.to_base(dolomite, Decimal("15"), "KG") == Decimal("15")


def test_alternative_units_convert_to_the_base_unit():
    dolomite = _product("Dolomite", "kg")
    catalog = _catalog([dolomite], [_unit(dolomite, "sack", "30"), _unit(dolomite, "g", "0.001")])
    assert catalog.to_base(dolomite, Decimal("2"), "sack") == Decimal("60.000")
    assert catalog.to_base(dolomite, Decimal("500"), "g") == Decimal("0.500")


def test_an_unknown_unit_is_rejected_and_says_which_ones_exist():
    dolomite = _product("Dolomite", "kg")
    catalog = _catalog([dolomite], [_unit(dolomite, "sack", "30")])
    with pytest.raises(ProductError, match="no unit 'drum'"):
        catalog.to_base(dolomite, Decimal("1"), "drum")


# --- recipe expansion ------------------------------------------------------


def test_a_stocked_product_expands_to_itself():
    dolomite = _product("Dolomite", "kg")
    assert expand(_catalog([dolomite]), [(dolomite.id, Decimal("15"))]) == {dolomite.id: Decimal("15")}


def test_a_mixture_expands_into_its_components_scaled_by_the_amount():
    """Mix A, 1 L = 2 g of B + 500 g of C. Applying 3 L needs 6 g and 1500 g."""
    mix = _product("Mix A", "L", kind="formula")
    b = _product("B", "g")
    c = _product("C", "g")
    catalog = _catalog([mix, b, c], components=[_component(mix, b, "2"), _component(mix, c, "500")])
    assert expand(catalog, [(mix.id, Decimal("3"))]) == {b.id: Decimal("6"), c.id: Decimal("1500")}


def test_untracked_components_like_water_never_reach_stock():
    mix = _product("Mix A", "L", kind="formula")
    b = _product("B", "g")
    water = _product("Water", "L", tracked=False)
    catalog = _catalog([mix, b, water], components=[_component(mix, b, "2"), _component(mix, water, "1")])
    assert expand(catalog, [(mix.id, Decimal("1"))]) == {b.id: Decimal("2")}


def test_a_product_reached_down_two_branches_is_summed_once():
    """One movement per item, or the second would start from a stale balance."""
    mix = _product("Mix A", "L", kind="formula")
    inner = _product("Inner", "L", kind="formula")
    shared = _product("Shared", "g")
    catalog = _catalog(
        [mix, inner, shared],
        components=[_component(mix, shared, "2"), _component(mix, inner, "1"), _component(inner, shared, "3")],
    )
    assert expand(catalog, [(mix.id, Decimal("1"))]) == {shared.id: Decimal("5")}


def test_nested_mixtures_multiply_through():
    outer = _product("Outer", "L", kind="formula")
    inner = _product("Inner", "L", kind="formula")
    leaf = _product("Leaf", "g")
    catalog = _catalog(
        [outer, inner, leaf], components=[_component(outer, inner, "2"), _component(inner, leaf, "50")]
    )
    assert expand(catalog, [(outer.id, Decimal("3"))]) == {leaf.id: Decimal("300")}


def test_separate_lines_of_the_same_product_are_summed():
    dolomite = _product("Dolomite", "kg")
    catalog = _catalog([dolomite])
    totals = expand(catalog, [(dolomite.id, Decimal("10")), (dolomite.id, Decimal("5"))])
    assert totals == {dolomite.id: Decimal("15")}


def test_expansion_needs_a_positive_amount():
    dolomite = _product("Dolomite", "kg")
    with pytest.raises(ProductError, match="above 0"):
        expand(_catalog([dolomite]), [(dolomite.id, Decimal("0"))])


def test_a_product_outside_the_catalog_is_rejected():
    with pytest.raises(ProductError, match="not in this farm's catalog"):
        expand(_catalog([]), [(uuid.uuid4(), Decimal("1"))])


def test_a_mixture_with_no_recipe_yet_consumes_nothing():
    mix = _product("Mix A", "L", kind="formula")
    assert expand(_catalog([mix]), [(mix.id, Decimal("5"))]) == {}


# --- cycles and depth ------------------------------------------------------


def test_a_recipe_that_contains_itself_is_caught_during_expansion():
    a = _product("A", "L", kind="formula")
    b = _product("B", "L", kind="formula")
    catalog = _catalog([a, b], components=[_component(a, b, "1"), _component(b, a, "1")])
    with pytest.raises(ProductError, match="contains itself"):
        expand(catalog, [(a.id, Decimal("1"))])


def test_check_recipe_rejects_a_product_listing_itself():
    a = _product("A", "L", kind="formula")
    with pytest.raises(ProductError, match="cannot contain itself"):
        check_recipe(_catalog([a]), a.id, [a.id])


def test_check_recipe_rejects_a_loop_before_it_is_saved():
    """B already contains A, so giving A a component of B would close the loop."""
    a = _product("A", "L", kind="formula")
    b = _product("B", "L", kind="formula")
    catalog = _catalog([a, b], components=[_component(b, a, "1")])
    with pytest.raises(ProductError, match="contains itself"):
        check_recipe(catalog, a.id, [b.id])


def test_check_recipe_accepts_a_recipe_with_no_loop():
    a = _product("A", "L", kind="formula")
    b = _product("B", "g")
    check_recipe(_catalog([a, b]), a.id, [b.id])


def test_recipes_nested_past_the_limit_are_rejected():
    chain = [_product(f"P{i}", "L", kind="formula") for i in range(MAX_DEPTH + 3)]
    components = [_component(chain[i], chain[i + 1], "1") for i in range(len(chain) - 1)]
    catalog = _catalog(chain, components=components)
    with pytest.raises(ProductError, match="deep"):
        expand(catalog, [(chain[0].id, Decimal("1"))])


# --- treatment payload -----------------------------------------------------


def test_a_treatment_needs_an_action_or_products():
    with pytest.raises(ValidationError, match="action or at least one product"):
        TreatmentCreate(treatment_time="06:30")


def test_free_text_treatments_still_work_without_a_warehouse():
    treatment = TreatmentCreate(treatment_time="06:30", action="Applied probiotic 2L per pond")
    assert treatment.items == [] and treatment.warehouse_id is None


def test_products_need_a_warehouse_to_come_out_of():
    with pytest.raises(ValidationError, match="which warehouse"):
        TreatmentCreate(
            treatment_time="06:30",
            items=[{"product_id": str(uuid.uuid4()), "amount": "2"}],
        )


def test_treatment_amounts_must_be_positive():
    with pytest.raises(ValidationError):
        TreatmentCreate(
            treatment_time="06:30",
            warehouse_id=str(uuid.uuid4()),
            items=[{"product_id": str(uuid.uuid4()), "amount": "0"}],
        )


# --- units are real measurements ---------------------------------------------


@pytest.mark.parametrize(
    "typed,stored",
    [("kg", "kg"), ("KG", "kg"), ("Kilogram", "kg"), ("gr", "g"), ("liter", "L"), ("L", "L"), ("ml", "mL"), ("pc", "pcs")],
)
def test_base_units_are_canonicalised(typed, stored):
    from app.schemas import ProductCreate

    product = ProductCreate(farm_id=uuid.uuid4(), name="Biolacto", category="probiotics", base_unit=typed)
    assert product.base_unit == stored


@pytest.mark.parametrize("packaging", ["sack", "jerrycan", "bottle", "drum"])
def test_packaging_is_not_a_base_unit(packaging):
    """A sack is not a measurement: it belongs in the unit list with a factor."""
    from app.schemas import ProductCreate

    with pytest.raises(ValidationError):
        ProductCreate(farm_id=uuid.uuid4(), name="Dolomite", category="lime_minerals", base_unit=packaging)


def test_packaging_converts_through_the_unit_list():
    dolomite = _product("Dolomite", "kg")
    catalog = _catalog([dolomite], [_unit(dolomite, "sack", "30")])
    assert catalog.to_base(dolomite, Decimal("2"), "sack") == Decimal("60")


# --- what it costs -----------------------------------------------------------


def test_a_product_costs_its_own_price():
    dolomite = _product("Dolomite", "kg", price="4000")
    assert cost_of(_catalog([dolomite]), dolomite.id) == Decimal("4000")


def test_a_formula_costs_the_sum_of_what_it_is_made_of():
    """Mix A, 1 L = 2 g B at 50/g + 500 g C at 4/g -> 100 + 2000."""
    b = _product("B", "g", price="50")
    c = _product("C", "g", price="4")
    mix = _product("Mix A", "L", kind="formula")
    catalog = _catalog([mix, b, c], components=[_component(mix, b, "2"), _component(mix, c, "500")])
    assert cost_of(catalog, mix.id) == Decimal("2100.00")


def test_an_unpriced_ingredient_makes_the_whole_cost_unknown():
    """Better no number than a total that quietly leaves something out."""
    b = _product("B", "g", price="50")
    c = _product("C", "g")
    mix = _product("Mix A", "L", kind="formula")
    catalog = _catalog([mix, b, c], components=[_component(mix, b, "2"), _component(mix, c, "500")])
    assert cost_of(catalog, mix.id) is None


def test_free_ingredients_do_not_make_the_cost_unknown():
    water = _product("Water", "L", tracked=False)
    b = _product("B", "g", price="50")
    mix = _product("Mix A", "L", kind="formula")
    catalog = _catalog([mix, b, water], components=[_component(mix, b, "2"), _component(mix, water, "1")])
    assert cost_of(catalog, mix.id) == Decimal("100.00")


def test_a_rebrand_costs_what_it_is_made_of():
    biolacto = _product("Biolacto", "g", price="120")
    lacto = _product("Lactobacillus", "g", kind="formula")
    catalog = _catalog([lacto, biolacto], components=[_component(lacto, biolacto, "1")])
    assert cost_of(catalog, lacto.id) == Decimal("120.00")


def test_a_formula_with_no_ingredients_has_no_cost():
    mix = _product("Mix A", "L", kind="formula")
    assert cost_of(_catalog([mix]), mix.id) is None


def test_an_unpriced_product_has_no_cost():
    assert cost_of(_catalog([(p := _product("X", "kg"))]), p.id) is None
