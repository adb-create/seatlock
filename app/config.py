"""Application settings, read from environment variables.

Plain os.environ keeps the dependency list short; every value has a safe
local-development default.
"""

import os


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://booking:booking@localhost:5432/booking",
        )
        # Empty REDIS_URL -> in-process rate limiter (fine for a single instance).
        self.redis_url: str = os.getenv("REDIS_URL", "")

        self.jwt_secret: str = os.getenv("JWT_SECRET", "change-me-in-production")
        self.jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
        self.pbkdf2_iterations: int = int(os.getenv("PBKDF2_ITERATIONS", "600000"))

        # How long a user may hold seats before paying.
        self.hold_ttl_seconds: int = int(os.getenv("HOLD_TTL_SECONDS", "300"))
        self.max_seats_per_booking: int = int(os.getenv("MAX_SEATS_PER_BOOKING", "10"))
        self.hold_rate_limit_per_minute: int = int(os.getenv("HOLD_RATE_LIMIT_PER_MINUTE", "20"))

        self.sweeper_enabled: bool = _bool("SWEEPER_ENABLED", True)
        self.sweeper_interval_seconds: int = int(os.getenv("SWEEPER_INTERVAL_SECONDS", "30"))

        self.db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "20"))
        self.db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "30"))


settings = Settings()
