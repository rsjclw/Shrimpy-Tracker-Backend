from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import additives, blind_feeding, cycles, days, farms, feed_types, grids, ponds

app = FastAPI(title="Shrimp Farm API", version="0.1.0")

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
