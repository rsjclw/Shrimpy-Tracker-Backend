from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def get_or_404(db: AsyncSession, model_class: type[T], model_id: Any, not_found_msg: str) -> T:
    instance = await db.get(model_class, model_id)
    if not instance:
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found_msg)
    return instance


def apply_updates(instance: Any, payload: Any, exclude_unset: bool = True) -> None:
    for k, v in payload.model_dump(exclude_unset=exclude_unset).items():
        setattr(instance, k, v)
