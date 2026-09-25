"""Unit conversion and formula expansion.

A log entry names catalog entries and amounts. Before any stock can move, those
lines have to become plain amounts of stocked *products* in their own base units:
formulas expand into what they are made of, other units convert to base units.
That is this module; moving the stock is services/inventory.py.
"""
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, ProductComponent, ProductUnit

# A formula nested deeper than this is a mistake, not a real one.
MAX_DEPTH = 10


class ProductError(ValueError):
    pass


class Catalog:
    """A farm's catalog, units and recipes, loaded once so expansion needs no I/O.

    Farms have tens of entries, not thousands, so loading the lot beats walking
    the recipe tree with a query per node.
    """

    def __init__(
        self,
        products: list[Product],
        units: list[ProductUnit],
        components: list[ProductComponent],
    ) -> None:
        self.products: dict[UUID, Product] = {p.id: p for p in products}
        self.units: dict[UUID, dict[str, Decimal]] = defaultdict(dict)
        for unit in units:
            self.units[unit.product_id][unit.unit.strip().lower()] = unit.factor_to_base
        self.components: dict[UUID, list[ProductComponent]] = defaultdict(list)
        for component in components:
            self.components[component.product_id].append(component)

    def get(self, product_id: UUID) -> Product:
        product = self.products.get(product_id)
        if product is None:
            raise ProductError(f"Product {product_id} is not in this farm's catalog")
        return product

    def factor(self, product: Product, unit: str | None) -> Decimal:
        """How many base units one `unit` of this product is."""
        if unit is None or not unit.strip():
            return Decimal("1")
        wanted = unit.strip().lower()
        if wanted == product.base_unit.strip().lower():
            return Decimal("1")
        factor = self.units[product.id].get(wanted)
        if factor is None:
            known = ", ".join(sorted({product.base_unit.strip().lower()} | set(self.units[product.id])))
            raise ProductError(f"{product.name} has no unit {unit!r}. Known units: {known}")
        return factor

    def to_base(self, product: Product, amount: Decimal, unit: str | None) -> Decimal:
        return amount * self.factor(product, unit)


async def load_catalog(db: AsyncSession, farm_id: UUID) -> Catalog:
    products = list((await db.execute(select(Product).where(Product.farm_id == farm_id))).scalars())
    ids = [p.id for p in products]
    if not ids:
        return Catalog([], [], [])
    units = list((await db.execute(select(ProductUnit).where(ProductUnit.product_id.in_(ids)))).scalars())
    components = list(
        (await db.execute(select(ProductComponent).where(ProductComponent.product_id.in_(ids)))).scalars()
    )
    return Catalog(products, units, components)


def tidy(value: Decimal) -> Decimal:
    """Drop trailing zeros a recipe's scale picked up, without going exponential.

    Multiplying through Numeric(14,4) ratios leaves 1500 looking like 1500.0000,
    and plain normalize() would turn that into 1.5E+3 on the way to the client.
    """
    return Decimal(format(value.normalize(), "f"))


def expand(catalog: Catalog, lines: list[tuple[UUID, Decimal]]) -> dict[UUID, Decimal]:
    """Turn (entry, base amount) lines into {stocked product: total base amount}.

    Formulas recurse into what they are made of; untracked entries (water) drop
    out. The same product reached down two branches is summed, so stock moves once.
    """
    totals: dict[UUID, Decimal] = defaultdict(Decimal)
    for product_id, amount in lines:
        if amount <= 0:
            raise ProductError("Enter an amount above 0")
        _accumulate(catalog, product_id, amount, totals, (), 0)
    return {pid: tidy(qty) for pid, qty in totals.items() if qty > 0}


def _accumulate(
    catalog: Catalog,
    product_id: UUID,
    amount: Decimal,
    totals: dict[UUID, Decimal],
    path: tuple[UUID, ...],
    depth: int,
) -> None:
    product = catalog.get(product_id)
    if not product.tracked:
        return
    if product.kind != "formula":
        totals[product_id] += amount
        return
    if product_id in path:
        names = " -> ".join(catalog.get(p).name for p in (*path, product_id))
        raise ProductError(f"This recipe contains itself: {names}")
    if depth >= MAX_DEPTH:
        raise ProductError(f"{product.name} nests recipes more than {MAX_DEPTH} deep")
    for component in catalog.components[product_id]:
        _accumulate(
            catalog,
            component.component_product_id,
            amount * component.quantity,
            totals,
            (*path, product_id),
            depth + 1,
        )


def check_recipe(catalog: Catalog, product_id: UUID, component_ids: list[UUID]) -> None:
    """Reject a recipe that would make a product contain itself, before it is saved.

    The catalog still holds the old recipe, so the proposed components are walked
    explicitly and everything below them comes from what is already stored.
    """
    for component_id in component_ids:
        if component_id == product_id:
            raise ProductError("A product cannot contain itself")
        _accumulate(catalog, component_id, Decimal("1"), defaultdict(Decimal), (product_id,), 1)


def cost_of(catalog: Catalog, product_id: UUID) -> Decimal | None:
    """What one base unit of this entry costs, from the prices of what it is made of.

    A product answers with its own price. A formula expands to products and sums.
    Returns None if anything it needs has no price, so a half-priced total is
    never passed off as the real one.
    """
    product = catalog.products.get(product_id)
    if product is None:
        return None
    if product.kind != "formula":
        return product.price_per_unit
    try:
        totals = expand(catalog, [(product_id, Decimal("1"))])
    except ProductError:
        return None
    if not totals:
        return None
    running = Decimal("0")
    for leaf_id, amount in totals.items():
        price = catalog.products[leaf_id].price_per_unit
        if price is None:
            return None
        running += price * amount
    return running.quantize(Decimal("0.01"))
