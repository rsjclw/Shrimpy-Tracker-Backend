import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    role: str


class FarmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FarmUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FarmDeleteOut(BaseModel):
    farm_id: uuid.UUID
    farm_name: str
    deleted: bool


class FarmMemberOut(BaseModel):
    farm_id: uuid.UUID
    email: str
    user_id: uuid.UUID | None
    role: str
    created_at: datetime


class FarmMemberCreate(BaseModel):
    email: str
    role: str


class RegisteredUserOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime
    last_sign_in_at: datetime | None = None
    is_admin: bool = False
