import json

import pytest
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient

import main
from main import app


@pytest.fixture
def api(monkeypatch):
    # decode_responses=True matches the production redis_client — without it
    # .get returns bytes and str-valued keys (pet_tts wav paths) behave
    # differently than in prod.
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(main, "redis_client", fake)
    monkeypatch.setattr(main, "DORF_API_TOKEN", None)
    monkeypatch.setattr(main, "RESPONSE_MAX_WAIT_S", 0.2)
    with TestClient(app) as c:
        yield c, fake


def test_health_ok(api):
    c, _ = api
    resp = c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "redis": True}


def test_process_query_pushes_plain_text(api):
    """The old code pushed f"godot_dwarf:{query}" — the LLM received the whole
    request dict's repr. The payload must be the bare query text. The
    "source": "godot" tag is what makes the response worker synthesize local
    pet speech (pet_tts:{uid}) instead of leaving it to the Discord voice
    path."""
    c, fake = api
    resp = c.post("/api/process_query", json={"query": "what is a dorf"})
    assert resp.status_code == 200
    unique_id = resp.json()["unique_id"]
    assert len(unique_id) == 32  # uuid4 hex
    raw = fake.rpop("response_queue")
    assert raw is not None
    payload = json.loads(raw)
    assert payload == {
        "unique_id": unique_id,
        "message": "what is a dorf",
        "source": "godot",
    }


def test_process_query_rejects_missing_field(api):
    c, _ = api
    resp = c.post("/api/process_query", json={"wrong": "field"})
    assert resp.status_code == 422


def test_process_query_ids_dont_collide(api):
    """md5-of-query reused ids for repeat queries and replayed stale answers."""
    c, _ = api
    a = c.post("/api/process_query", json={"query": "same"}).json()["unique_id"]
    b = c.post("/api/process_query", json={"query": "same"}).json()["unique_id"]
    assert a != b


def test_fetch_response_returns_and_deletes_key(api):
    c, fake = api
    fake.set("response:abc123", "the answer")
    resp = c.post("/api/fetch_response", json={"unique_id": "abc123"})
    assert resp.status_code == 200
    assert resp.json() == {"response": "the answer"}
    # The old code deleted the bare id, never the fetched key — repeat polls
    # replayed the same answer until the 3600s TTL.
    assert fake.get("response:abc123") is None


def test_fetch_response_times_out(api):
    c, _ = api
    resp = c.post("/api/fetch_response", json={"unique_id": "nope"})
    assert resp.status_code == 504


def test_avatar_state(api):
    c, fake = api
    assert c.get("/api/avatar_state").json() == {"state": "idle"}
    fake.set("avatar_state", "talking")
    assert c.get("/api/avatar_state").json() == {"state": "talking"}


def test_token_auth_rejects_missing_header(api, monkeypatch):
    monkeypatch.setattr(main, "DORF_API_TOKEN", "sekrit")
    c, _ = api
    resp = c.post("/api/process_query", json={"query": "hi"})
    assert resp.status_code == 401


def test_token_auth_accepts_valid_header(api, monkeypatch):
    monkeypatch.setattr(main, "DORF_API_TOKEN", "sekrit")
    c, _ = api
    resp = c.post(
        "/api/process_query",
        json={"query": "hi"},
        headers={"X-Dorf-Token": "sekrit"},
    )
    assert resp.status_code == 200


def test_tts_missing_key_404(api):
    """Unknown unique_id (no pet_tts key at all) → 404, never a 500."""
    c, _ = api
    resp = c.get("/api/tts/nope")
    assert resp.status_code == 404


def test_tts_failed_marker_404(api):
    """The response worker's "failed" marker must read as text-only, not as
    an audio response the pet would try to decode."""
    c, fake = api
    fake.set("pet_tts:abc123", "failed")
    resp = c.get("/api/tts/abc123")
    assert resp.status_code == 404


def test_tts_serves_wav_bytes(api, tmp_path):
    c, fake = api
    # 44-byte canonical 16-bit PCM wav (sine placeholder via stdlib wave).
    import io
    import struct
    import wave

    wav_path = tmp_path / "pet_abc123.wav"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<h", 0))
    wav_path.write_bytes(buf.getvalue())

    fake.set("pet_tts:abc123", str(wav_path))
    resp = c.get("/api/tts/abc123")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == wav_path.read_bytes()


def test_tts_file_gone_404(api, tmp_path):
    """Wav deleted while the key is still alive (retention edge) → 404
    instead of FileResponse raising a 500."""
    c, fake = api
    fake.set("pet_tts:abc123", str(tmp_path / "vanished.wav"))
    resp = c.get("/api/tts/abc123")
    assert resp.status_code == 404


def test_tts_requires_token(api, monkeypatch):
    monkeypatch.setattr(main, "DORF_API_TOKEN", "sekrit")
    c, _ = api
    assert c.get("/api/tts/abc123").status_code == 401
    assert (
        c.get("/api/tts/abc123", headers={"X-Dorf-Token": "sekrit"}).status_code
        == 404  # auth passed; key simply doesn't exist
    )
