import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlindFeedingTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    daily_feed_per_100k: list[float] = Field(min_length=1)

    @field_validator("daily_feed_per_100k")
    @classmethod
    def validate_rates(cls, values: list[float]) -> list[float]:
        if any(value < 0 for value in values):
            raise ValueError("Daily feed values cannot be negative")
        return values


class BlindFeedingTemplateCreate(BlindFeedingTemplateBase):
    farm_id: uuid.UUID


class BlindFeedingTemplateUpdate(BlindFeedingTemplateBase):
    pass


class BlindFeedingTemplateOut(BlindFeedingTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    created_at: datetime
    duration_days: int
    cumulative_feed_per_100k: float
