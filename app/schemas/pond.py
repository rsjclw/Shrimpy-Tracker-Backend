import uuid
from datetime import time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_POND_FEED_TIME = time(6, 0)


class PondSize(BaseModel):
    """Width and length in metres, or just an area for a pond nobody has measured.

    With both width and length, area_m2 is always their product (an area sent
    alongside is ignored), so the three can never disagree.
    """

    area_m2: Decimal | None = None
    width_m: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    length_m: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)

    @model_validator(mode="after")
    def area_from_size(self) -> "PondSize":
        if (self.width_m is None) != (self.length_m is None):
            raise ValueError("Give both width_m and length_m, or neither")
        if self.width_m is not None and self.length_m is not None:
            self.area_m2 = (self.width_m * self.length_m).quantize(Decimal("0.01"))
        return self


class PondCreate(PondSize):
    grid_id: uuid.UUID
    name: str
    default_feed_time: time = DEFAULT_POND_FEED_TIME


class PondUpdate(PondSize):
    name: str
    default_feed_time: time = DEFAULT_POND_FEED_TIME


class PondOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grid_id: uuid.UUID
    name: str
    area_m2: Decimal | None
    width_m: Decimal | None = None
    length_m: Decimal | None = None
    default_feed_time: time
    # Set from the inventory page, per warehouse. Null: this pond moves no stock.
    warehouse_id: uuid.UUID | None = None
