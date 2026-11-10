"""Tests run against a real PostgreSQL database.

SQLite would silently ignore SELECT ... FOR UPDATE, so the concurrency tests
would pass for the wrong reason. Point TEST_DATABASE_URL at a throwaway DB
(docker compose creates `booking_test` for you). It is wiped on every run.
"""

import os

# Must be set before anything from `app` is imported.
os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://booking:booking@localhost:5432/booking_test"
)
os.environ["SWEEPER_ENABLED"] = "false"
os.environ["PBKDF2_ITERATIONS"] = "1000"  # fast hashing in tests only
os.environ["REDIS_URL"] = ""
os.environ["DB_POOL_SIZE"] = "50"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.rate_limit import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE booking_seats, seats, bookings, events, users RESTART IDENTITY CASCADE")
        )
    limiter.reset()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
