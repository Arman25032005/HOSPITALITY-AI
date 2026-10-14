import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from app.core import metrics

log = logging.getLogger("hospitality.ratelimit")


class RateLimiter:
    """Sliding-window limiter, in-process. Correct for a single API process only; use the Redis
    backend (RATE_LIMIT_BACKEND=redis) when running more than one replica."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1]) * 1000
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"""


class RedisRateLimiter:
    """Shared sliding window (sorted set + Lua so check-and-add is atomic, using Redis's own
    clock). If Redis is unreachable the limiter FAILS OPEN and counts the error, so an outage of
    the limiter cannot take the product down."""

    def __init__(self, client, prefix: str = "rl:"):
        self.client, self.prefix = client, prefix
        self._script = client.register_script(_LUA)

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        try:
            return bool(self._script(keys=[self.prefix + key],
                                     args=[window, limit, uuid.uuid4().hex]))
        except Exception:
            metrics.RATE_LIMIT_ERRORS.inc()
            log.error("rate limit backend unavailable; allowing request", exc_info=False)
            return True

    def reset(self) -> None:
        for k in self.client.scan_iter(match=self.prefix + "*"):
            self.client.delete(k)


class _Limiter:
    """Process-wide handle; `configure` selects the backend at startup."""

    def __init__(self):
        self.backend: RateLimiter | RedisRateLimiter = RateLimiter()

    def configure(self, settings) -> None:
        if settings.rate_limit_backend == "redis":
            import redis

            self.backend = RedisRateLimiter(redis.Redis.from_url(
                settings.redis_url, socket_timeout=0.25, socket_connect_timeout=0.25))
        else:
            self.backend = RateLimiter()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        return self.backend.allow(key, limit, window)

    def reset(self) -> None:
        self.backend.reset()


limiter = _Limiter()
