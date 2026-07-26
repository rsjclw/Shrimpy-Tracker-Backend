import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import additives, blind_feeding, cycles, days, farms, feed_types, grids, ponds
from app.services import weather_sync

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task: asyncio.Task | None = None
    if settings.weather_enabled:
        task = asyncio.create_task(weather_sync.run_forever())
        logger.info("Weather sweep started (%s local)", settings.weather_sync_hours)
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Shrimp Farm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(grids.router)
app.include_router(farms.router)
app.include_router(ponds.router)
app.include_router(cycles.router)
app.include_router(days.router)
app.include_router(feed_types.router)
app.include_router(additives.router)
app.include_router(blind_feeding.router)
