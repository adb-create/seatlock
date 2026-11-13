import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import auth, bookings, events
from app.services.booking_service import BookingError, expire_stale_holds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seatlock")


def _sweep_once() -> int:
    with SessionLocal() as db:
        return expire_stale_holds(db)


async def _sweeper_loop() -> None:
    while True:
        await asyncio.sleep(settings.sweeper_interval_seconds)
        try:
            released = await asyncio.to_thread(_sweep_once)
            if released:
                logger.info("Sweeper expired %d stale hold(s)", released)
        except Exception:  # keep the loop alive whatever happens
            logger.exception("Sweeper run failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # For a project this size create_all is enough; see README for Alembic.
    Base.metadata.create_all(engine)
    task = asyncio.create_task(_sweeper_loop()) if settings.sweeper_enabled else None
    yield
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="SeatLock",
    summary="Concurrency-safe seat booking API",
    description=(
        "Hold seats, pay, cancel, without ever selling one seat twice. "
        "Click **Authorize** and paste the token from `/auth/login`."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(BookingError)
async def booking_error_handler(_: Request, exc: BookingError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


app.include_router(auth.router)
app.include_router(events.router)
app.include_router(bookings.router)

# ---------- web interface ----------
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    """The booking website (a single page that talks to the API above)."""
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
