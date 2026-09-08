import asyncio
import json
import os
import uuid
from pathlib import Path

import redis
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

# Load the api/.env that lives next to this file so the service is not
# CWD-sensitive (running `uvicorn main:app` from the repo root previously
# loaded the root .env and missed REDIS_* entirely).
load_dotenv(Path(__file__).resolve().parent / ".env")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Optional shared secret: the Godot client sends it as the X-Dorf-Token
# header (DORF_API_TOKEN env there). Unset in api/.env disables the check.
DORF_API_TOKEN = os.getenv("DORF_API_TOKEN") or None

RESPONSE_QUEUE = "response_queue"
RESPONSE_KEY_PREFIX = "response"
AVATAR_STATE_KEY = "avatar_state"
# How long fetch_response waits for the bot's LLM answer. Env-tunable so
# contract tests can shrink it.
RESPONSE_MAX_WAIT_S = float(os.getenv("RESPONSE_MAX_WAIT_S", "125"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
)

app = FastAPI()


async def require_token(request: Request) -> None:
    """Reject requests without the shared token when one is configured.

    Without this, any local process could inject prompts that get spoken in
    a Discord voice channel and posted in chat.
    """
    if DORF_API_TOKEN and request.headers.get("x-dorf-token") != DORF_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


@app.get("/api/avatar_state")
async def avatar_state() -> dict:
    """Current avatar state for the Godot pet (written by the bot to Redis).

    Falls back to "idle" when the key is missing (bot down / TTL expired) or
    Redis is unreachable — the pet must never error on a failed read.
    """
    try:
        state = await asyncio.to_thread(redis_client.get, AVATAR_STATE_KEY)
    except Exception:
        state = None
    return {"state": state if state else "idle"}


@app.get("/api/health")
async def health() -> dict:
    try:
        await asyncio.to_thread(redis_client.ping)
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok}


def generate_unique_id() -> str:
    """uuid4, matching the bot side (md5-of-query collided on repeats)."""
    return uuid.uuid4().hex


@app.post("/api/process_query")
async def process_query(query: dict, _: None = Depends(require_token)):
    text = query.get("query")
    if not text or not isinstance(text, str):
        raise HTTPException(status_code=422, detail="Missing 'query' string field")
    unique_id = generate_unique_id()
    # source: "godot" is the pet's only entry point; the response worker uses
    # it to synthesize local speech (pet_tts:{uid}) instead of relying on the
    # command-side Discord voice path. Absence of the tag = Discord text
    # command, behavior unchanged.
    payload = json.dumps(
        {"unique_id": unique_id, "message": text, "source": "godot"}
    )
    await asyncio.to_thread(redis_client.lpush, RESPONSE_QUEUE, payload)
    return {"unique_id": unique_id}


@app.post("/api/fetch_response")
async def get_result(unique_id: dict, _: None = Depends(require_token)) -> dict:
    key_id = unique_id.get("unique_id")
    if not key_id or not isinstance(key_id, str):
        raise HTTPException(status_code=422, detail="Missing 'unique_id' string field")
    return await poll_redis_for_key(key_id)


@app.get("/api/tts/{unique_id}")
async def get_tts(unique_id: str, _: None = Depends(require_token)):
    """Serve the pet's local speech for a query (written by the response
    worker after the LLM).

    The pet_tts:{uid} value is an absolute wav path — the api process reads
    it from disk. "failed" / missing / file-gone all 404 so the pet treats
    it as text-only and gives up fast.
    """
    try:
        wav_path = await asyncio.to_thread(
            redis_client.get, f"pet_tts:{unique_id}"
        )
    except Exception:
        wav_path = None
    if not wav_path or wav_path == "failed":
        raise HTTPException(status_code=404, detail="No TTS for this query")
    path = Path(wav_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No TTS for this query")
    return FileResponse(path, media_type="audio/wav")


async def poll_redis_for_key(
    key: str,
    *,
    poll_interval_s: float = 0.5,
    max_wait_s: float | None = None,
) -> dict:
    """Poll `response:{key}` for up to max_wait_s, then 504.

    Mirrors the bot's poll_redis_for_key_with_timeout (utilities.py): the
    response key carries a 3600s TTL, but an unbounded wait left Godot clients
    hanging forever whenever the response worker was down.
    """
    if max_wait_s is None:
        max_wait_s = RESPONSE_MAX_WAIT_S
    redis_key = f"{RESPONSE_KEY_PREFIX}:{key}"
    deadline = asyncio.get_running_loop().time() + max_wait_s
    while True:
        response = await asyncio.to_thread(redis_client.get, redis_key)
        if response is not None:
            # Consume the key so repeat polls don't replay a stale answer.
            await asyncio.to_thread(redis_client.delete, redis_key)
            return {"response": response}
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(
                status_code=504, detail="Timed out waiting for a response"
            )
        await asyncio.sleep(poll_interval_s)
