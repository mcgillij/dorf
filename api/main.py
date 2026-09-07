import asyncio
import json
import os
import uuid
from pathlib import Path

import redis
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

# Load the api/.env that lives next to this file so the service is not
# CWD-sensitive (running `uvicorn main:app` from the repo root previously
# loaded the root .env and missed REDIS_* entirely).
load_dotenv(Path(__file__).resolve().parent / ".env")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

RESPONSE_QUEUE = "response_queue"
RESPONSE_KEY_PREFIX = "response"

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
)

app = FastAPI()


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
async def process_query(query: dict):
    text = query.get("query")
    if not text or not isinstance(text, str):
        raise HTTPException(status_code=422, detail="Missing 'query' string field")
    unique_id = generate_unique_id()
    payload = json.dumps({"unique_id": unique_id, "message": text})
    await asyncio.to_thread(redis_client.lpush, RESPONSE_QUEUE, payload)
    return {"unique_id": unique_id}


@app.post("/api/fetch_response")
async def get_result(unique_id: dict) -> dict:
    key_id = unique_id.get("unique_id")
    if not key_id or not isinstance(key_id, str):
        raise HTTPException(status_code=422, detail="Missing 'unique_id' string field")
    return await poll_redis_for_key(key_id)


async def poll_redis_for_key(
    key: str,
    *,
    poll_interval_s: float = 0.5,
    max_wait_s: float = 125.0,
) -> dict:
    """Poll `response:{key}` for up to max_wait_s, then 504.

    Mirrors the bot's poll_redis_for_key_with_timeout (utilities.py): the
    response key carries a 3600s TTL, but an unbounded wait left Godot clients
    hanging forever whenever the response worker was down.
    """
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
