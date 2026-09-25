"""What a cycle has dosed into its feed, and at what strength.

The additive catalog is gone: an additive is any catalog entry (a product or a
formula) that carries a dose per kg of feed. These two shapes are what the
dashboards read.
"""
import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class AdditiveDoseOut(BaseModel):
    """The dose a cycle is on for one catalog entry, as of a date.

    Only entries the cycle has already used appear: the dose a new feeding gets
    when it leaves the dose out is whatever was last given here.
    """

    # Null only for entries on pre-merge feedings whose name matches nothing.
    product_id: uuid.UUID | None
    name: str
    dose_per_kg: Decimal
    # What that dose is measured in, per kg of feed: g, mL or pcs.
    dose_unit: str
    last_used_date: date


class AdditiveUsageOut(BaseModel):
    """One day and one entry: the feed that carried it, and how much went in."""

    date: date
    product_id: uuid.UUID | None
    name: str
    feed_kg: Decimal
    amount_g: Decimal
    dosage_gr_per_kg: Decimal
