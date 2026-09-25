from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import Grid, InventoryItem, InventoryMovement, Pond, Product, ProductComponent, ProductUnit, Warehouse
from app.schemas import (
    InventoryItemCreate,
    InventoryItemOut,
    InventoryItemUpdate,
    MovementCreate,
    MovementOut,
    WarehouseCreate,
    WarehousePonds,
    WarehouseInventoryOut,
    WarehouseOut,
    WarehouseUpdate,
)
from app.services.access import require_farm_permission, require_grid_permission
from app.services.common import apply_updates, get_or_404
from app.services.inventory import MovementError, apply_movement

router = APIRouter(tags=["inventory"])

_WAREHOUSE_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "A warehouse with that name already exists on this grid")
_ITEM_CONFLICT = HTTPException(status.HTTP_409_CONFLICT, "This warehouse already stocks that item")


async def _require_warehouse(
    db: AsyncSession, user: CurrentUser, warehouse_id: UUID, permission: str
) -> tuple[Warehouse, UUID]:
    """The warehouse and the farm it belongs to, once the permission is checked."""
    warehouse = await get_or_404(db, Warehouse, warehouse_id, "Warehouse not found")
    grid = await get_or_404(db, Grid, warehouse.grid_id, "Grid not found")
    await require_farm_permission(db, user, grid.farm_id, permission)
    return warehouse, grid.farm_id


async def _require_item(db: AsyncSession, user: CurrentUser, item_id: UUID, permission: str) -> InventoryItem:
    item = await get_or_404(db, InventoryItem, item_id, "Item not found")
    await _require_warehouse(db, user, item.warehouse_id, permission)
    return item


async def _check_product(db: AsyncSession, farm_id: UUID, product_id: UUID) -> Product:
    """A stock row may only hold a product of its own farm, never a formula.

    A formula is never held in a warehouse: it expands into the products it is
    made of when applied, so stock of one would never be drawn down.
    """
    product = await get_or_404(db, Product, product_id, "Product not found")
    if product.farm_id != farm_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That product belongs to another farm")
    if product.kind == "formula":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{product.name} is a formula, so it is never stocked. Stock the products it is made of instead.",
        )
    return product


async def _ponds_by_warehouse(db: AsyncSession, grid_id: UUID) -> dict[UUID, list[UUID]]:
    result = await db.execute(
        select(Pond.warehouse_id, Pond.id).where(Pond.grid_id == grid_id, Pond.warehouse_id.isnot(None))
    )
    out: dict[UUID, list[UUID]] = {}
    for warehouse_id, pond_id in result.all():
        out.setdefault(warehouse_id, []).append(pond_id)
    return out


def _warehouse_out(warehouse: Warehouse, ponds: dict[UUID, list[UUID]]) -> dict:
    data = {c.name: getattr(warehouse, c.name) for c in warehouse.__table__.columns}
    data["pond_ids"] = ponds.get(warehouse.id, [])
    if hasattr(warehouse, "items"):
        data["items"] = warehouse.items
    return data


async def _with_product(db: AsyncSession, item_id: UUID) -> InventoryItem:
    """Re-read a stock row with its product loaded: the response needs the name,
    and a lazy load would blow up on the async session."""
    result = await db.execute(
        select(InventoryItem).where(InventoryItem.id == item_id).options(joinedload(InventoryItem.product))
    )
    return result.scalar_one()


async def _commit(db: AsyncSession, conflict: HTTPException) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise conflict


@router.get("/grids/{grid_id}/inventory", response_model=list[WarehouseInventoryOut])
async def grid_inventory(
    grid_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Warehouse]:
    """Every warehouse on the grid with its items - the whole inventory page in one request."""
    await require_grid_permission(db, user, grid_id)
    result = await db.execute(
        select(Warehouse)
        .where(Warehouse.grid_id == grid_id)
        .options(selectinload(Warehouse.items).joinedload(InventoryItem.product))
        .order_by(Warehouse.created_at, Warehouse.name)
    )
    warehouses = list(result.scalars().all())
    for w in warehouses:
        w.items.sort(key=lambda i: (i.product.category, i.product.name.lower()))
    return [_warehouse_out(w, await _ponds_by_warehouse(db, grid_id)) for w in warehouses]


@router.post("/grids/{grid_id}/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
async def create_warehouse(
    grid_id: UUID,
    payload: WarehouseCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Warehouse:
    await require_grid_permission(db, user, grid_id, "manage")
    data = payload.model_dump()
    copy_from = data.pop("copy_items_from")
    warehouse = Warehouse(grid_id=grid_id, **data)
    db.add(warehouse)
    await db.flush()
    if copy_from is not None:
        source, _ = await _require_warehouse(db, user, copy_from, "manage")
        if source.grid_id != grid_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That warehouse is on another grid")
        # The same products, all at zero: what is held here is counted here.
        rows = await db.execute(select(InventoryItem.product_id).where(InventoryItem.warehouse_id == copy_from))
        for product_id in rows.scalars():
            db.add(InventoryItem(warehouse_id=warehouse.id, product_id=product_id, quantity=0))
    await _commit(db, _WAREHOUSE_CONFLICT)
    await db.refresh(warehouse)
    return _warehouse_out(warehouse, {})


@router.put("/warehouses/{warehouse_id}", response_model=WarehouseOut)
async def update_warehouse(
    warehouse_id: UUID,
    payload: WarehouseUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Warehouse:
    warehouse, _ = await _require_warehouse(db, user, warehouse_id, "manage")
    apply_updates(warehouse, payload)
    await _commit(db, _WAREHOUSE_CONFLICT)
    await db.refresh(warehouse)
    return _warehouse_out(warehouse, await _ponds_by_warehouse(db, warehouse.grid_id))


@router.put("/warehouses/{warehouse_id}/ponds", response_model=WarehouseOut)
async def set_warehouse_ponds(
    warehouse_id: UUID,
    payload: WarehousePonds,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Say which ponds draw from this warehouse, replacing the current set.

    A pond draws from one warehouse, so naming it here takes it off whichever
    warehouse had it before. Ponds left out that pointed here are unassigned and
    stop moving stock.
    """
    warehouse, _ = await _require_warehouse(db, user, warehouse_id, "manage")
    if payload.pond_ids:
        known = await db.execute(
            select(Pond.id).where(Pond.id.in_(payload.pond_ids), Pond.grid_id == warehouse.grid_id)
        )
        if set(known.scalars()) != set(payload.pond_ids):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Every pond must be on the same grid as the warehouse",
            )
    await db.execute(
        update(Pond).where(Pond.warehouse_id == warehouse_id).values(warehouse_id=None)
    )
    if payload.pond_ids:
        await db.execute(
            update(Pond).where(Pond.id.in_(payload.pond_ids)).values(warehouse_id=warehouse_id)
        )
    await db.commit()
    return _warehouse_out(warehouse, await _ponds_by_warehouse(db, warehouse.grid_id))


@router.delete("/warehouses/{warehouse_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_warehouse(
    warehouse_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    warehouse, _ = await _require_warehouse(db, user, warehouse_id, "manage")
    await db.delete(warehouse)
    await db.commit()


@router.post("/warehouses/{warehouse_id}/items", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    warehouse_id: UUID,
    payload: InventoryItemCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> InventoryItem:
    _, farm_id = await _require_warehouse(db, user, warehouse_id, "manage")
    data = payload.model_dump(exclude={"new_product"})
    opening = data.pop("quantity")
    if payload.new_product is not None:
        # Creating a product only happens here, so one can never exist unstocked.
        spec = payload.new_product
        product = Product(
            farm_id=farm_id,
            kind="product",
            name=spec.name,
            category=spec.category,
            base_unit=spec.base_unit,
            price_per_unit=spec.price_per_unit,
        )
        base = spec.base_unit.strip().lower()
        seen: set[str] = set()
        for unit in spec.units:
            key = unit.unit.strip().lower()
            if key == base or key in seen:
                continue
            seen.add(key)
            product.units.append(ProductUnit(unit=unit.unit.strip(), factor_to_base=unit.factor_to_base))
        db.add(product)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, f"{spec.name} is already on this farm")
        data["product_id"] = product.id
    else:
        await _check_product(db, farm_id, payload.product_id)
    item = InventoryItem(warehouse_id=warehouse_id, quantity=opening, **data)
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise _ITEM_CONFLICT
    if opening > 0:
        # The opening amount is a stock count, so the history explains where it came from.
        db.add(
            InventoryMovement(
                item_id=item.id,
                kind="count",
                delta=opening,
                quantity_after=opening,
                note="Opening stock",
                created_by=user.email or user.id,
            )
        )
    await _commit(db, _ITEM_CONFLICT)
    return await _with_product(db, item.id)


@router.put("/inventory-items/{item_id}", response_model=InventoryItemOut)
async def update_item(
    item_id: UUID,
    payload: InventoryItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> InventoryItem:
    item = await _require_item(db, user, item_id, "manage")
    apply_updates(item, payload)
    await _commit(db, _ITEM_CONFLICT)
    return await _with_product(db, item.id)


@router.delete("/inventory-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    item = await _require_item(db, user, item_id, "manage")
    others = await db.execute(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.product_id == item.product_id, InventoryItem.id != item_id)
    )
    last_one = others.scalar_one() == 0
    if last_one:
        # Nothing would hold it any more, so the product goes too rather than
        # lingering somewhere nobody can see it.
        used_in = await db.execute(
            select(Product.name)
            .join(ProductComponent, ProductComponent.product_id == Product.id)
            .where(ProductComponent.component_product_id == item.product_id)
            .order_by(Product.name)
        )
        if recipes := list(used_in.scalars()):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This is the last stock of it, and it is an ingredient of: {', '.join(recipes)}. "
                "Remove it there first.",
            )
        product = await db.get(Product, item.product_id)
    await db.delete(item)
    if last_one and product is not None:
        await db.delete(product)
    await db.commit()


@router.post("/inventory-items/{item_id}/movements", response_model=InventoryItemOut)
async def add_movement(
    item_id: UUID,
    payload: MovementCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> InventoryItem:
    """Operators receive and use stock; a stock count (setting the exact amount) needs a maintainer."""
    await _require_item(db, user, item_id, "manage" if payload.kind == "count" else "add")
    # Lock the row so two people logging at once cannot both start from the same amount.
    result = await db.execute(
        select(InventoryItem).where(InventoryItem.id == item_id).with_for_update().execution_options(populate_existing=True)
    )
    item = result.scalar_one()
    try:
        delta, new = apply_movement(item.quantity, payload.kind, payload.amount)
    except MovementError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    item.quantity = new
    db.add(
        InventoryMovement(
            item_id=item.id,
            kind=payload.kind,
            delta=delta,
            quantity_after=new,
            note=(payload.note or "").strip() or None,
            created_by=user.email or user.id,
        )
    )
    await db.commit()
    return await _with_product(db, item.id)


@router.get("/inventory-items/{item_id}/movements", response_model=list[MovementOut])
async def list_movements(
    item_id: UUID,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[InventoryMovement]:
    await _require_item(db, user, item_id, "read")
    result = await db.execute(
        select(InventoryMovement)
        .where(InventoryMovement.item_id == item_id)
        .order_by(InventoryMovement.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
