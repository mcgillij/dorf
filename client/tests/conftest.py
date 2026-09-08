import importlib
import sys
from pathlib import Path

import pytest

# Make `import bot...` work when pytest runs from client/ (or anywhere).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _FakeRedis:
    """Minimal in-memory stand-in so tests can never reach production Redis.

    test_statemanager drives statemanager.update_state, which (since the
    Redis-backed avatar state) also writes a real `avatar_state` key — a
    bare `pytest` run once set avatar_state=talking in production for an
    hour and the desktop pet talked nonstop. The sqlite side is isolated
    via monkeypatched paths; redis is isolated here instead.
    """

    def __init__(self):
        self._data = {}

    def set(self, name, value, ex=None):
        self._data[name] = value
        return True

    def get(self, name):
        return self._data.get(name)

    def delete(self, *names):
        for name in names:
            self._data.pop(name, None)

    def ttl(self, name):
        return -1 if name in self._data else -2

    def ping(self):
        return True

    def exists(self, name):
        return name in self._data

    def llen(self, name):
        return 0

    def lrange(self, name, start, end):
        return []

    def lpush(self, name, value):
        return 1

    def rpop(self, name):
        return None


@pytest.fixture(autouse=True)
def _isolate_redis(monkeypatch):
    """Replace every imported redis client with _FakeRedis for the test.

    Modules bind `from bot.redis_client import redis_client` at import, so
    both the source module and each consumer's binding get patched.
    """
    fake = _FakeRedis()
    import bot.redis_client as redis_module

    monkeypatch.setattr(redis_module, "redis_client", fake)
    for modname in (
        "bot.statemanager",
        "bot.misc",
        "bot.audio_capture",
        "bot.utilities",
        "bot.db",
    ):
        try:
            module = importlib.import_module(modname)
        except Exception:
            continue
        if hasattr(module, "redis_client"):
            monkeypatch.setattr(module, "redis_client", fake)
    yield
