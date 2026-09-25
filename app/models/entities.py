import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Grid(Base):
    __tablename__ = "grids"

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # Both resolved from the coordinates by Open-Meteo's timezone=auto, then
    # cached here. The lunar windows and the weather schedule both read this.
    timezone: Mapped[str | None] = mapped_column(String(64))
    elevation_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    weather_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    farm: Mapped["Farm"] = relationship(back_populates="grids")
    ponds: Mapped[list["Pond"]] = relationship(back_populates="grid", cascade="all, delete-orphan")
    environment: Mapped[list["DailyEnvironment"]] = relationship(
        back_populates="grid", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    grids: Mapped[list[Grid]] = relationship(back_populates="farm", cascade="all, delete-orphan")
    memberships: Mapped[list["FarmMembership"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )


class FarmMembership(Base):
    __tablename__ = "farm_memberships"
    __table_args__ = (UniqueConstraint("farm_id", "email", name="uq_farm_memberships_farm_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    farm: Mapped[Farm] = relationship(back_populates="memberships")


class Pond(Base):
    __tablename__ = "ponds"

    id: Mapped[uuid.UUID] = _uuid_pk()
    grid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grids.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    area_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    default_feed_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(6, 0))
    # Where feed and treatments on this pond come out of. Null: nothing is deducted.
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    grid: Mapped[Grid] = relationship(back_populates="ponds")
    cycles: Mapped[list["Cycle"]] = relationship(back_populates="pond", cascade="all, delete-orphan")


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    pond_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ponds.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    planned_end_date: Mapped[date | None] = mapped_column(Date)
    actual_end_date: Mapped[date | None] = mapped_column(Date)
    initial_population: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_abw_g: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    maximum_daily_feed_capacity_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    stable_carrying_capacity_kg_per_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    final_carrying_capacity_kg_per_m3: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    feeding_index_increment: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), default=Decimal("0.010"), nullable=False
    )
    maximum_feeding_index: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    blind_feeding_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("blind_feeding_templates.id", ondelete="SET NULL")
    )
    blind_feeding_target_abw_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    prediction_config: Mapped[dict | None] = mapped_column(JSONB)

    pond: Mapped[Pond] = relationship(back_populates="cycles")
    daily_logs: Mapped[list["DailyLog"]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan"
    )
    population_samples: Mapped[list["PopulationSample"]] = relationship(
        back_populates="cycle", cascade="all, delete-orphan"
    )
    blind_feeding_template: Mapped["BlindFeedingTemplate | None"] = relationship()


class DailyLog(Base):
    __tablename__ = "daily_logs"
    __table_args__ = (UniqueConstraint("cycle_id", "date", name="uq_daily_logs_cycle_date"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    abw_g: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    abw_sample_time: Mapped[time | None] = mapped_column(Time)
    notes: Mapped[str | None] = mapped_column(Text)

    cycle: Mapped[Cycle] = relationship(back_populates="daily_logs")
    feedings: Mapped[list["FeedingSession"]] = relationship(
        back_populates="daily_log", cascade="all, delete-orphan"
    )
    water: Mapped["WaterParameters | None"] = relationship(
        back_populates="daily_log", cascade="all, delete-orphan", uselist=False
    )
    treatments: Mapped[list["Treatment"]] = relationship(
        back_populates="daily_log", cascade="all, delete-orphan"
    )
    harvests: Mapped[list["Harvest"]] = relationship(
        back_populates="daily_log", cascade="all, delete-orphan"
    )


class FeedingSession(Base):
    __tablename__ = "feeding_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    daily_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_logs.id", ondelete="CASCADE"), nullable=False
    )
    feed_time: Mapped[time] = mapped_column(Time, nullable=False)
    amount_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    duration_min: Mapped[int | None] = mapped_column(Integer)
    additives: Mapped[list[dict]] = mapped_column(JSON, default=list)
    feed_types: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Free-text label of who last wrote this row ("Ayu", "Worker 2", "Claude", ...),
    # paired with updated_by_type so the AI auto-fill can tell it must not
    # overwrite a human's entry - see routers/days.py::update_feeding.
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_by_type: Mapped[str | None] = mapped_column(String(20))

    daily_log: Mapped[DailyLog] = relationship(back_populates="feedings")


class WaterParameters(Base):
    __tablename__ = "water_parameters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    daily_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("daily_logs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    do_am: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    do_pm: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    ph_am: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    ph_pm: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    water_clarity_am: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    water_clarity_pm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    salinity: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    tan: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    nitrite: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    phosphate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    calcium: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    magnesium: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    alkalinity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    plankton_ga: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_bga: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_diatom: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_yga: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_eugle: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_dino: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_zoo: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    plankton_protozoa: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    yellow_vibrio: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    green_vibrio: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    black_vibrio: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tbc: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    daily_log: Mapped[DailyLog] = relationship(back_populates="water")


class Harvest(Base):
    __tablename__ = "harvests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    daily_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_logs.id", ondelete="CASCADE"), nullable=False
    )
    harvest_time: Mapped[time] = mapped_column(Time, nullable=False)
    biomass_kg: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    sampled_abw_g: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    estimated_count: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    daily_log: Mapped[DailyLog] = relationship(back_populates="harvests")


class Treatment(Base):
    __tablename__ = "treatments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    daily_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_logs.id", ondelete="CASCADE"), nullable=False
    )
    treatment_time: Mapped[time] = mapped_column(Time, nullable=False)
    # Stays required and is filled from `items` when the caller sends products
    # instead of prose, so the day view and the existing log UI keep working.
    action: Mapped[str] = mapped_column(Text, nullable=False)
    worker: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    # Which warehouse the stock came out of; null for a treatment logged as text only.
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    # The products applied, resolved at save time, same idea as FeedingSession.additives:
    # [{product_id, name, amount, unit, base_amount, base_unit}]
    items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    daily_log: Mapped[DailyLog] = relationship(back_populates="treatments")


class PopulationSample(Base):
    __tablename__ = "population_samples"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cycles.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    population: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)

    cycle: Mapped[Cycle] = relationship(back_populates="population_samples")




class DailyEnvironment(Base):
    """Cached daily weather for a grid.

    Keyed by grid rather than daily_log: every pond and cycle under a grid
    shares the same weather, so hanging it off daily logs would duplicate the
    same values per cycle and let the copies drift apart.
    """

    __tablename__ = "daily_environment"
    __table_args__ = (UniqueConstraint("grid_id", "date", name="uq_daily_environment_grid_date"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    grid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    temp_min_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    temp_max_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    temp_mean_c: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Global horizontal irradiance: the solar energy actually landing on the
    # pond surface. This is the "how much sun" number, not cloud cover.
    shortwave_radiation_sum_mj: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    sunshine_duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cloud_cover_daylight_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    precipitation_mm: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    precipitation_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    precipitation_probability_max_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="open-meteo")
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    grid: Mapped[Grid] = relationship(back_populates="environment")


class BlindFeedingTemplate(Base):
    __tablename__ = "blind_feeding_templates"
    __table_args__ = (
        UniqueConstraint("farm_id", "name", name="uq_blind_feeding_templates_farm_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    daily_feed_per_100k: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """One entry in the farm catalog. Two kinds, kept apart in the UI.

    A **product** is a thing you buy and keep: Biolacto, Dolomite. It has a maker
    and a price, warehouses hold stock of it, and a stock row says nothing more
    than where and how much.

    A **treatment formula** is what actually goes in the pond, named for what it
    does: Lactobacillus, or Mix A. It is never stocked. Applying one expands
    through `product_components` into the products it is made of, which is how a
    single log entry draws on several at once. A plain rebrand is a formula with
    one line: Lactobacillus = 1 Biolacto.

    The `treatments` table is a different thing: the *log* of what was applied to
    one pond on one day. A formula is what it was made of.

    Quantities are always stored in `base_unit`; other units are accepted on
    input and converted through `product_units`.
    """

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="product")  # product | formula
    base_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    # What one base unit costs. Set on products; a formula's cost is a sum over
    # its ingredients, so it is left null there.
    price_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # False for water and anything else worth naming in a recipe but never counted.
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    units: Mapped[list["ProductUnit"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    components: Mapped[list["ProductComponent"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="ProductComponent.product_id",
    )

    __table_args__ = (
        UniqueConstraint("farm_id", "name", name="uq_products_farm_name"),
    )


class ProductUnit(Base):
    """An alternative unit for a product: how many base units one of them is.

    A product kept in kg that arrives in 30 kg sacks gets ("sack", 30), so stock
    can be received in sacks and dosed in kg against the same balance.
    """

    __tablename__ = "product_units"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    factor_to_base: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    product: Mapped[Product] = relationship(back_populates="units")

    __table_args__ = (UniqueConstraint("product_id", "unit", name="uq_product_units_product_unit"),)


class ProductComponent(Base):
    """One line of what a treatment formula is made of.

    `quantity` is in the component's base unit, per 1 base unit of the parent:
    a 1 L formula of 2 g B and 500 g C is two rows of 2 and 500.
    """

    __tablename__ = "product_components"

    id: Mapped[uuid.UUID] = _uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT, not CASCADE: silently dropping a line would leave the recipe
    # looking complete while quietly dosing less than it says.
    component_product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    product: Mapped[Product] = relationship(back_populates="components", foreign_keys=[product_id])
    component: Mapped[Product] = relationship(foreign_keys=[component_product_id])

    __table_args__ = (
        UniqueConstraint("product_id", "component_product_id", name="uq_product_components_pair"),
    )


class Warehouse(Base):
    """A storage building on a grid. A grid may have several; stock is counted per warehouse."""

    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    grid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grids.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="warehouse", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (UniqueConstraint("grid_id", "name", name="uq_warehouses_grid_name"),)


class InventoryItem(Base):
    """How much of one product a warehouse holds. Nothing about what it *is*.

    Name, category and unit live on the product this points at, so the same
    goods in two warehouses cannot drift into two different descriptions.
    Quantities are in the product's `base_unit`.
    """

    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False, default=Decimal("0"))
    low_stock_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    # Where in the warehouse it sits; for finding things, never for counting.
    location_note: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    warehouse: Mapped[Warehouse] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()

    __table_args__ = (UniqueConstraint("warehouse_id", "product_id", name="uq_inventory_items_warehouse_product"),)


class InventoryMovement(Base):
    """Every stock change, so the amount on hand always has a history of who changed it and why."""

    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # receive | use | count
    delta: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    quantity_after: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # What caused this movement, so editing or deleting that thing can put the
    # stock back. Null for the manual receive/use/count a person enters by hand.
    source_type: Mapped[str | None] = mapped_column(String(20))  # treatment
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
