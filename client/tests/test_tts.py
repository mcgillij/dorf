"""TTS provider registry + response-worker pet branch.

Torch-free by design: these tests import bot.tts.base (and the lazy
bot.tts entry point) and must never trigger a backend import — see
docs/modular_tts.md §2/§7. FakeProviders are registered inside a fixture
(never at module import) and the registry/instance cache is snapshotted and
restored around each test so nothing leaks into the global registry.
"""

import asyncio
import io
import json
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.tts import get_provider
from bot.tts.base import PROVIDER_CLASSES, TTSProvider, register_tts
import bot.tts as tts_pkg

RESPONSE_WORKER = "bot.workers.process_response_worker"


def _tiny_wav(path: str) -> None:
    """Write a valid 16-bit PCM wav with stdlib only — no backends."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<h", 0))
    Path(path).write_bytes(buf.getvalue())


class _FakeRedis:
    """In-memory redis stand-in: set/get/ex plus a scripted rpop queue."""

    def __init__(self, items=None):
        self._data = {}
        self._queue = list(items or [])

    def set(self, name, value, ex=None):
        self._data[name] = value
        return True

    def get(self, name):
        return self._data.get(name)

    def rpop(self, name):
        return self._queue.pop(0) if self._queue else None

    def lpush(self, name, value):
        self._queue.insert(0, value)
        return 1


@pytest.fixture
def isolated_registry():
    """Snapshot PROVIDER_CLASSES + instance cache; restore on teardown."""
    classes = dict(PROVIDER_CLASSES)
    instances = dict(tts_pkg._INSTANCES)
    yield
    PROVIDER_CLASSES.clear()
    PROVIDER_CLASSES.update(classes)
    tts_pkg._INSTANCES.clear()
    tts_pkg._INSTANCES.update(instances)


@pytest.fixture
def fake_provider(isolated_registry):
    @register_tts
    class FakeProvider(TTSProvider):
        name = "fake"

        def synthesize(self, text: str, voice: str, output_wav: str) -> None:
            _tiny_wav(output_wav)

    return FakeProvider


def test_bot_tts_import_stays_backend_free():
    """The whole lazy-import trick: importing bot.tts must not load any
    backend (torch weight) — only get_provider() may."""
    import bot.tts  # noqa: F401

    assert "bot.tts.base" in sys.modules
    assert not any(
        m == "kokoro" or m.startswith("kokoro.") or m == "torch"
        for m in sys.modules
    )


def test_get_provider_selects_and_caches(fake_provider):
    p1 = get_provider("fake")
    p2 = get_provider("fake")
    assert isinstance(p1, fake_provider)
    assert p1 is p2  # instance cache, not a new provider per call


def test_provider_selection_via_env(fake_provider, monkeypatch):
    """get_provider() with no name falls back to the TTS_PROVIDER binding —
    the env-driven engine swap must need zero call-site changes."""
    monkeypatch.setattr(tts_pkg, "TTS_PROVIDER", "fake")
    assert isinstance(tts_pkg.get_provider(), fake_provider)


def test_unknown_provider_raises_with_available_list(fake_provider):
    with pytest.raises(KeyError) as excinfo:
        get_provider("bogus")
    assert "fake" in str(excinfo.value)  # the available list is in the error


def _worker_mod():
    import importlib

    return importlib.import_module(RESPONSE_WORKER)


def _run_one_task(worker, items, bot_stub, monkeypatch, tmp_path, provider):
    # Queue items are JSON strings (the worker json.loads them).
    monkeypatch.setattr(
        worker, "redis_client", _FakeRedis([json.dumps(i) for i in items])
    )

    def _synth(text, voice, output_wav):
        provider().synthesize(text, voice, output_wav)

    monkeypatch.setattr(worker, "synthesize", _synth)
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            asyncio.wait_for(
                worker.process_response_queue("response_queue", "response", bot_stub),
                timeout=2.5,
            )
        )


def _bot_stub(response: str):
    async def get_response(message):
        return response

    return SimpleNamespace(llm=SimpleNamespace(get_response=get_response))


def test_godot_source_synthesizes_and_publishes_key(
    fake_provider, monkeypatch, tmp_path
):
    worker = _worker_mod()
    _run_one_task(
        worker,
        [dict(unique_id="u1", message="hi", source="godot")],
        _bot_stub("hello pet"),
        monkeypatch,
        tmp_path,
        fake_provider,
    )
    fake = worker.redis_client
    assert fake.get("response:u1") == "hello pet"
    wav = fake.get("pet_tts:u1")
    assert wav is not None and Path(wav).is_absolute()
    assert wav == str(tmp_path / "pet_u1.wav")
    assert Path(wav).exists()  # the fake provider really wrote the wav


def test_godot_source_empty_response_marks_failed(fake_provider, monkeypatch, tmp_path):
    worker = _worker_mod()
    _run_one_task(
        worker,
        [dict(unique_id="u2", message="hi", source="godot")],
        _bot_stub(""),
        monkeypatch,
        tmp_path,
        fake_provider,
    )
    fake = worker.redis_client
    assert fake.get("response:u2") == ""
    assert fake.get("pet_tts:u2") == "failed"


def test_godot_source_synth_failure_marks_failed(
    fake_provider, monkeypatch, tmp_path
):
    class ExplodingProvider(TTSProvider):
        name = "explode"

        def synthesize(self, text, voice, output_wav):
            raise RuntimeError("backend exploded")

    worker = _worker_mod()
    _run_one_task(
        worker,
        [dict(unique_id="u3", message="hi", source="godot")],
        _bot_stub("hello pet"),
        monkeypatch,
        tmp_path,
        ExplodingProvider,
    )
    assert worker.redis_client.get("pet_tts:u3") == "failed"


def test_discord_source_untouched(monkeypatch, tmp_path):
    """No source tag = Discord text command: response published, pet_tts
    never touched, synthesize never called."""

    def must_not_be_called():
        raise AssertionError("provider must not resolve for non-godot sources")

    worker = _worker_mod()
    _run_one_task(
        worker,
        [dict(unique_id="u4", message="hi")],
        _bot_stub("hello discord"),
        monkeypatch,
        tmp_path,
        must_not_be_called,
    )
    fake = worker.redis_client
    assert fake.get("response:u4") == "hello discord"
    assert "pet_tts:u4" not in fake._data
