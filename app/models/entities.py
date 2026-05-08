import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    farm: Mapped["Farm"] = relationship(back_populates="grids")
    ponds: Mapped[list["Pond"]] = relationship(back_populates="grid", cascade="all, delete-orphan")


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
    salinity: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    tan: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    nitrite: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    phosphate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    calcium: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    magnesium: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    alkalinity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

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
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
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
    action: Mapped[str] = mapped_column(Text, nullable=False)
    worker: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

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


class FeedAdditive(Base):
    __tablename__ = "feed_additives"
    __table_args__ = (UniqueConstraint("farm_id", "name", name="uq_feed_additives_farm_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    dosage_gr_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))


class FeedType(Base):
    __tablename__ = "feed_types"

    id: Mapped[uuid.UUID] = _uuid_pk()
    farm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("farms.id", ondelete="CASCADE"), nullable=False
    )
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    price_per_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
