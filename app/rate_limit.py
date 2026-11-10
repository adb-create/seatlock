"""Fixed-window rate limiter.

Uses Redis when REDIS_URL is set, so the limit is shared by every API
instance; otherwise falls back to an in-process counter. If Redis goes down
the limiter fails open: we would rather let a few extra requests through than
take booking offline.
"""

import logging
import threading
import time

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, redis_url: str = "") -> None:
        self._redis = None
        if redis_url:
            import redis  # imported lazily so Redis stays optional

            self._redis = redis.Redis.from_url(redis_url, socket_timeout=0.5)
        self._local: dict[str, int] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        bucket = int(time.time() // window_seconds)
        bucket_key = f"rl:{key}:{bucket}"

        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(bucket_key)
                pipe.expire(bucket_key, window_seconds * 2)
                count, _ = pipe.execute()
                return int(count) <= limit
            except Exception:  # noqa: BLE001 - any Redis failure -> fail open
                logger.warning("Redis unavailable, rate limiter failing open", exc_info=True)
                return True

        with self._lock:
            # Drop counters from old windows so memory stays bounded.
            suffix = f":{bucket}"
            if len(self._local) > 10_000:
                self._local = {k: v for k, v in self._local.items() if k.endswith(suffix)}
            count = self._local.get(bucket_key, 0) + 1
            self._local[bucket_key] = count
        return count <= limit

    def reset(self) -> None:
        with self._lock:
            self._local.clear()


limiter = RateLimiter(settings.redis_url)
