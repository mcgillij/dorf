from fastapi import FastAPI
import os
import hashlib
import json
import asyncio

import redis
from dotenv import load_dotenv

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def generate_unique_id(message: str) -> str:
    """Generates a unique ID based on message from godot."""
    return hashlib.md5(f"godot_dwarf^{message}".encode()).hexdigest()


app = FastAPI()


@app.post("/api/process_query")
async def process_query(query: dict):
    print(f"Processing query: {query}")
    unique_id = generate_unique_id(query["query"])
    print(f"Unique ID: {unique_id}")
    redis_client.lpush(
        "response_queue",
        json.dumps({"unique_id": unique_id, "message": f"godot_dwarf:{query}"}),
    )
    return {"unique_id": unique_id}


@app.post("/api/fetch_response")
async def get_result(unique_id: dict) -> dict:
    return await poll_redis_for_key(unique_id["unique_id"])


async def poll_redis_for_key(key: str, timeout: float = 0.5) -> dict:
    """Polls Redis for a key and returns its value when found."""
    while True:
        print(f"Polling Redis for key: {key}")
        response = redis_client.get(f"response:{key}")
        if response:
            redis_client.delete(key)
            return {
                "response": f"{json.dumps(response.decode("utf-8") if isinstance(response, bytes) else response)}"
            }
        await asyncio.sleep(timeout)
