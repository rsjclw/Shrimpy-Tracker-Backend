import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field, field_validator


PLANKTON_FIELDS = (
    "plankton_ga",
    "plankton_bga",
    "plankton_diatom",
    "plankton_yga",
    "plankton_eugle",
    "plankton_dino",
    "plankton_zoo",
    "plankton_protozoa",
)

VIBRIO_FIELDS = ("yellow_vibrio", "green_vibrio", "black_vibrio")
BACTERIA_FIELDS = (*VIBRIO_FIELDS, "tbc")
BIOLOGY_SOURCE_FIELDS = (*PLANKTON_FIELDS, *BACTERIA_FIELDS)
BIOLOGY_COMPUTED_FIELDS = ("total_plankton", "total_vibrio_count", "vibrio_percentage")


def _nullable_sum(values: list[Decimal | None]) -> Decimal | None:
    if all(value is None for value in values):
        return None
    return sum((value or Decimal("0")) for value in values)


def _field_values(source: Any, fields: tuple[str, ...]) -> list[Decimal | None]:
    return [getattr(source, field, None) for field in fields]


def total_plankton(source: Any) -> Decimal | None:
    return _nullable_sum(_field_values(source, PLANKTON_FIELDS))


def total_vibrio_count(source: Any) -> Decimal | None:
    return _nullable_sum(_field_values(source, VIBRIO_FIELDS))


def vibrio_percentage(source: Any) -> Decimal | None:
    total_vibrio = total_vibrio_count(source)
    tbc = getattr(source, "tbc", None)
    if total_vibrio is None or tbc is None or tbc == 0:
        return None
    return (total_vibrio / tbc * Decimal("100")).quantize(Decimal("0.01"))


def water_metric_value(source: Any, metric: str) -> Decimal | None:
    if metric == "total_plankton":
        return total_plankton(source)
    if metric == "total_vibrio_count":
        return total_vibrio_count(source)
    if metric == "vibrio_percentage":
        return vibrio_percentage(source)
    return getattr(source, metric, None)


class WaterParametersUpsert(BaseModel):
    do_am: Decimal | None = None
    do_pm: Decimal | None = None
    ph_am: Decimal | None = None
    ph_pm: Decimal | None = None
    water_clarity_am: Decimal | None = None
    water_clarity_pm: Decimal | None = None
    salinity: Decimal | None = None
    tan: Decimal | None = None
    nitrite: Decimal | None = None
    phosphate: Decimal | None = None
    calcium: Decimal | None = None
    magnesium: Decimal | None = None
    alkalinity: Decimal | None = None
    plankton_ga: Decimal | None = None
    plankton_bga: Decimal | None = None
    plankton_diatom: Decimal | None = None
    plankton_yga: Decimal | None = None
    plankton_eugle: Decimal | None = None
    plankton_dino: Decimal | None = None
    plankton_zoo: Decimal | None = None
    plankton_protozoa: Decimal | None = None
    yellow_vibrio: Decimal | None = None
    green_vibrio: Decimal | None = None
    black_vibrio: Decimal | None = None
    tbc: Decimal | None = None

    @field_validator(*BIOLOGY_SOURCE_FIELDS)
    @classmethod
    def biology_counts_must_be_non_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("must be non-negative")
        return value


class WaterParametersOut(WaterParametersUpsert):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    daily_log_id: uuid.UUID

    @computed_field
    @property
    def total_plankton(self) -> Decimal | None:
        return total_plankton(self)

    @computed_field
    @property
    def total_vibrio_count(self) -> Decimal | None:
        return total_vibrio_count(self)

    @computed_field
    @property
    def vibrio_percentage(self) -> Decimal | None:
        return vibrio_percentage(self)
