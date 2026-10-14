import shutil
import socket
import subprocess
import threading
import time

import pytest
import redis

from app.config import Settings
from app.conversations.ratelimit import RateLimiter, RedisRateLimiter, _Limiter
from app.core import metrics

pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None,
                                reason="redis-server is not installed")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url():
    port = free_port()
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no",
                             "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL)
    url = f"redis://127.0.0.1:{port}/0"
    for _ in range(50):
        try:
            redis.Redis.from_url(url).ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture()
def rl(redis_url):
    client = redis.Redis.from_url(redis_url)
    limiter = RedisRateLimiter(client, prefix=f"t{time.time_ns()}:")
    yield limiter
    limiter.reset()


def test_limit_window_and_key_isolation(rl):
    assert [rl.allow("a", 3, 60) for _ in range(5)] == [True, True, True, False, False]
    assert rl.allow("b", 3, 60)  # separate key
    assert [rl.allow("short", 1, 1) for _ in range(2)] == [True, False]
    time.sleep(1.2)
    assert rl.allow("short", 1, 1)  # the window slid


def test_limit_is_atomic_under_concurrency(rl):
    results = []

    def hit():
        results.append(rl.allow("race", 10, 60))

    ts = [threading.Thread(target=hit) for _ in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 10


def test_replicas_share_one_budget(redis_url):
    a = RedisRateLimiter(redis.Redis.from_url(redis_url), prefix="shared:")
    b = RedisRateLimiter(redis.Redis.from_url(redis_url), prefix="shared:")
    try:
        assert [a.allow("k", 2, 60), b.allow("k", 2, 60), a.allow("k", 2, 60)] == [True, True, False]
    finally:
        a.reset()


def test_fails_open_and_counts_when_redis_is_down():
    dead = RedisRateLimiter(redis.Redis(host="127.0.0.1", port=free_port(), socket_connect_timeout=0.1,
                                        socket_timeout=0.1))
    before = metrics.RATE_LIMIT_ERRORS._value.get()
    assert dead.allow("x", 1, 60) and dead.allow("x", 1, 60)  # never blocks traffic
    assert metrics.RATE_LIMIT_ERRORS._value.get() == before + 2


def test_backend_selection(redis_url):
    lim = _Limiter()
    assert isinstance(lim.backend, RateLimiter)
    lim.configure(Settings(rate_limit_backend="redis", redis_url=redis_url))
    assert isinstance(lim.backend, RedisRateLimiter)
    assert lim.allow("sel", 1, 60) and not lim.allow("sel", 1, 60)
    lim.reset()
    lim.configure(Settings(rate_limit_backend="memory"))
    assert isinstance(lim.backend, RateLimiter)
