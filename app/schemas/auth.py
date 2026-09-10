import uuid

from pydantic import BaseModel, Field

from app.schemas.farm import RegisteredUserOut

# bcrypt only looks at the first 72 bytes of a password.
PASSWORD_MAX_LENGTH = 72
PASSWORD_MIN_LENGTH = 8


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool = False
    must_change_password: bool = False


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class UserCreate(BaseModel):
    email: str
    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class UserCreatedOut(BaseModel):
    user: RegisteredUserOut
    temporary_password: str | None = None


class ResetPasswordRequest(BaseModel):
    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class ResetPasswordOut(BaseModel):
    temporary_password: str | None = None


class UserActiveUpdate(BaseModel):
    is_active: bool
