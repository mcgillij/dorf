import json

import pytest
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient

import main
from main import app


@pytest.fixture
def api(monkeypatch):
    fake = FakeStrictRedis()
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
    request dict's repr. The payload must be the bare query text."""
    c, fake = api
    resp = c.post("/api/process_query", json={"query": "what is a dorf"})
    assert resp.status_code == 200
    unique_id = resp.json()["unique_id"]
    assert len(unique_id) == 32  # uuid4 hex
    raw = fake.rpop("response_queue")
    assert raw is not None
    payload = json.loads(raw)
    assert payload == {"unique_id": unique_id, "message": "what is a dorf"}


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
