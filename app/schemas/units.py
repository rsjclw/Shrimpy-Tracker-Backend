"""Units, shared by the catalog and the stocking call so neither imports the other."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Stock is always kept in a real measurement unit, so amounts from different
# warehouses and different recipes can be added up. How it is bought or talked
# about - sacks, jerrycans, bottles - goes in product_units with a factor.
BaseUnit = Literal["kg", "g", "L", "mL", "pcs"]

# What people actually type, mapped to the one spelling stored.
_BASE_UNIT_ALIASES = {
    "kg": "kg", "kilo": "kg", "kilogram": "kg", "kgs": "kg",
    "g": "g", "gr": "g", "gram": "g", "grams": "g",
    "l": "L", "lt": "L", "ltr": "L", "liter": "L", "litre": "L", "liters": "L",
    "ml": "mL", "milliliter": "mL", "millilitre": "mL", "cc": "mL",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "pcs", "ea": "pcs",
}


def canonical_base_unit(value: str) -> str:
    """Accept the spellings people use, store one of them."""
    if not isinstance(value, str):
        return value
    return _BASE_UNIT_ALIASES.get(value.strip().lower(), value.strip())


def required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ProductUnitIn(BaseModel):
    """How it is bought or spoken about: one `unit` is `factor_to_base` base units.

    Free text on purpose - a sack, a jerrycan and a bottle are real to the people
    counting them, and only the factor has to be exact.
    """

    unit: str = Field(max_length=30)
    factor_to_base: Decimal = Field(gt=0)

    _unit = field_validator("unit")(required_text)


# A dose is "so much per kg of feed", in the unit the thing is practically
# measured in: grams for a mass, millilitres for a volume. Derived from the
# product so it is never a choice, and never a bare "g" on a liquid.
_DOSE_UNITS = {"kg": "g", "g": "g", "L": "mL", "mL": "mL", "pcs": "pcs"}


def dose_unit_for(base_unit: str) -> str:
    return _DOSE_UNITS.get((base_unit or "").strip(), (base_unit or "").strip())


# How many of a product's base units one dose unit is: 1 g is 0.001 kg.
_PER_DOSE_UNIT = {"kg": Decimal("0.001"), "g": Decimal("1"), "L": Decimal("0.001"), "mL": Decimal("1"), "pcs": Decimal("1")}


def base_units_per_dose_unit(base_unit: str) -> Decimal:
    """Convert a dose amount into the product's own unit.

    Dosing 100 g/kg of something counted in kg, over 50 kg of feed, is 5000 g -
    which is 5 kg off the shelf, not 5000. Getting this wrong is a 1000x error.
    """
    return _PER_DOSE_UNIT.get((base_unit or "").strip(), Decimal("1"))
