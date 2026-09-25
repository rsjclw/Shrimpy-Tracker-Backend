"""The farm catalog: the products it buys, and the formulas it makes from them.

A product is stocked in warehouses. A treatment formula is what goes in the pond
and is never stocked; applying it expands into the products it is made of. Nothing
here moves stock - that happens when a day's treatment is logged.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Grid, InventoryItem, Product, ProductComponent, ProductUnit, Warehouse
from app.schemas import (
    ExpansionLine,
    ExpansionOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)
from app.schemas.product import ProductComponentIn, ProductKind, ProductUnitIn
from app.services.access import accessible_farm_ids, require_farm_permission
from app.services.common import get_or_404
from app.services.products import Catalog, ProductError, check_recipe, cost_of, expand, load_catalog

router = APIRouter(tags=["products"])

_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "A product with that name already exists on this farm")
_LOADED = (selectinload(Product.units), selectinload(Product.components))


def _as_out(product: Product, names: dict[UUID, Product], catalog: Catalog | None = None) -> ProductOut:
    """Serialise an entry, naming each ingredient so a formula reads on its own."""
    out = ProductOut.model_validate(product)
    if catalog is not None:
        out.cost_per_unit = cost_of(catalog, product.id)
    for line in out.components:
        component = names.get(line.component_product_id)
        if component:
            line.component_name = component.name
            line.component_base_unit = component.base_unit
    out.components.sort(key=lambda c: (c.component_name or "").lower())
    out.units.sort(key=lambda u: u.unit.lower())
    return out


async def _name_map(db: AsyncSession, farm_id: UUID) -> dict[UUID, Product]:
    result = await db.execute(select(Product).where(Product.farm_id == farm_id))
    return {p.id: p for p in result.scalars()}


async def _require_product(
    db: AsyncSession, user: CurrentUser, product_id: UUID, permission: str
) -> Product:
    result = await db.execute(
        select(Product).where(Product.id == product_id).options(*_LOADED)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    await require_farm_permission(db, user, product.farm_id, permission)
    return product


async def _validate_components(
    db: AsyncSession, product: Product, components: list[ProductComponentIn]
) -> None:
    """Every component must be a product of the same farm, and must not loop back."""
    if not components:
        return
    ids = [c.component_product_id for c in components]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The same product is listed twice")
    result = await db.execute(select(Product.id).where(Product.id.in_(ids), Product.farm_id == product.farm_id))
    known = set(result.scalars())
    if missing := [str(i) for i in ids if i not in known]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Not a product of this farm: {', '.join(missing)}",
        )
    catalog = await load_catalog(db, product.farm_id)
    try:
        check_recipe(catalog, product.id, ids)
    except ProductError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _set_units(product: Product, units: list[ProductUnitIn]) -> None:
    """Add the unit rows. Any old ones must already be cleared *and flushed*."""
    base = product.base_unit.strip().lower()
    seen: set[str] = set()
    for unit in units:
        key = unit.unit.strip().lower()
        if key == base or key in seen:
            # The base unit is implicitly 1; listing it again would be a second truth.
            continue
        seen.add(key)
        product.units.append(ProductUnit(unit=unit.unit.strip(), factor_to_base=unit.factor_to_base))


def _set_components(product: Product, components: list[ProductComponentIn]) -> None:
    """Add the recipe rows. Any old ones must already be cleared *and flushed*."""
    for line in components:
        product.components.append(
            ProductComponent(component_product_id=line.component_product_id, quantity=line.quantity)
        )


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    farm_id: UUID | None = None,
    include_inactive: bool = False,
    kind: ProductKind | None = Query(None, description="'product' for what you buy, 'formula' for what you apply"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[ProductOut]:
    if farm_id:
        await require_farm_permission(db, user, farm_id)
        farm_ids = [farm_id]
    else:
        farm_ids = list(await accessible_farm_ids(db, user))
        if not farm_ids:
            return []
    stmt = select(Product).where(Product.farm_id.in_(farm_ids)).options(*_LOADED)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    if kind:
        stmt = stmt.where(Product.kind == kind)
    result = await db.execute(stmt.order_by(Product.category, Product.name))
    products = list(result.scalars())
    names = {p.id: p for p in products}
    for product in products:
        for line in product.components:
            names.setdefault(line.component_product_id, None)  # type: ignore[arg-type]
    missing = [pid for pid, value in names.items() if value is None]
    if missing:
        extra = await db.execute(select(Product).where(Product.id.in_(missing)))
        names.update({p.id: p for p in extra.scalars()})
    # One catalog per farm prices every formula without a query per row.
    catalogs = {fid: await load_catalog(db, fid) for fid in {p.farm_id for p in products}}
    return [_as_out(p, names, catalogs.get(p.farm_id)) for p in products]


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ProductOut:
    await require_farm_permission(db, user, payload.farm_id, "manage")
    if payload.kind != "formula":
        # A product exists because a warehouse holds it. Stock it, and it is created.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Only formulas are created here. A product is created by stocking it in a warehouse.",
        )
    fields = payload.model_dump(exclude={"units", "components"})
    product = Product(**fields)
    await _validate_components(db, product, payload.components)
    _set_units(product, payload.units)
    _set_components(product, payload.components)
    db.add(product)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    return await get_product(product.id, db, user)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ProductOut:
    product = await _require_product(db, user, product_id, "read")
    return _as_out(product, await _name_map(db, product.farm_id), await load_catalog(db, product.farm_id))


@router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ProductOut:
    product = await _require_product(db, user, product_id, "manage")
    fields = payload.model_dump(exclude_unset=True, exclude={"units", "components"})
    for key, value in fields.items():
        setattr(product, key, value)
    # Read what the units should become before clearing them.
    units: list[ProductUnitIn] | None = payload.units
    if units is None and "base_unit" in fields:
        # The new base unit must not also sit in the alternatives at some other factor.
        units = [ProductUnitIn(unit=u.unit, factor_to_base=u.factor_to_base) for u in product.units]

    if payload.components is not None:
        await _validate_components(db, product, payload.components)
        product.components.clear()
    if units is not None:
        product.units.clear()
    # Delete the old rows before inserting the new ones. In one flush SQLAlchemy is
    # free to insert first, and a line kept unchanged then collides with itself on
    # the unique pair - which surfaced as a bogus "already exists" on every edit.
    await db.flush()
    if payload.components is not None:
        _set_components(product, payload.components)
    if units is not None:
        _set_units(product, units)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _CONFLICT
    return await get_product(product_id, db, user)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    product = await _require_product(db, user, product_id, "manage")
    used_in = await db.execute(
        select(Product.name)
        .join(ProductComponent, ProductComponent.product_id == Product.id)
        .where(ProductComponent.component_product_id == product_id)
        .order_by(Product.name)
    )
    if recipes := list(used_in.scalars()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{product.name} is an ingredient of: {', '.join(recipes)}. Remove it there first.",
        )
    # Stock that no longer knows what it is would be worse than refusing the delete.
    held = await db.execute(
        select(Warehouse.name)
        .join(InventoryItem, InventoryItem.warehouse_id == Warehouse.id)
        .where(InventoryItem.product_id == product_id)
        .order_by(Warehouse.name)
    )
    if warehouses := list(held.scalars()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{product.name} is still stocked in: {', '.join(warehouses)}. Remove the stock first.",
        )
    await db.delete(product)
    await db.commit()


@router.get("/products/{product_id}/expand", response_model=ExpansionOut)
async def expand_product(
    product_id: UUID,
    amount: Decimal = Query(..., gt=0),
    unit: str | None = Query(None),
    warehouse_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ExpansionOut:
    """What applying this much of a product would actually take out of stock.

    With a `warehouse_id` each line also says how much is on hand, so the UI can
    warn before anyone saves rather than failing on submit.
    """
    product = await _require_product(db, user, product_id, "read")
    catalog = await load_catalog(db, product.farm_id)
    try:
        base = catalog.to_base(product, amount, unit)
        totals = expand(catalog, [(product_id, base)])
    except ProductError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    on_hand: dict[UUID, Decimal] = {}
    if warehouse_id and totals:
        warehouse = await get_or_404(db, Warehouse, warehouse_id, "Warehouse not found")
        grid = await get_or_404(db, Grid, warehouse.grid_id, "Grid not found")
        await require_farm_permission(db, user, grid.farm_id, "read")
        stock = await db.execute(
            select(InventoryItem.product_id, InventoryItem.quantity).where(
                InventoryItem.warehouse_id == warehouse_id,
                InventoryItem.product_id.in_(list(totals)),
            )
        )
        on_hand = {pid: qty for pid, qty in stock.all()}

    lines = []
    for pid, qty in totals.items():
        leaf = catalog.get(pid)
        available = on_hand.get(pid)
        lines.append(
            ExpansionLine(
                product_id=pid,
                name=leaf.name,
                amount=qty,
                unit=leaf.base_unit,
                in_stock=available,
                enough=available is not None and available >= qty if warehouse_id else True,
            )
        )
    lines.sort(key=lambda line: line.name.lower())
    return ExpansionOut(
        product_id=product_id,
        amount=amount,
        unit=(unit or product.base_unit),
        lines=lines,
    )
