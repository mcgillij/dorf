import os
import re
import json
from random import randint
import logging
import asyncio
import aiohttp
from bot.db import SQLiteDB
from bot.redis_client import redis_client
from bot.constants import WHISPER_QUEUE, VOICE_RESPONSE_QUEUE, VOICE_NIC_RESPONSE_QUEUE

logger = logging.getLogger(__name__)


bot_name_pattern = re.compile(r"\b(bot|derf|derfbot|dorf|dwarf)\b", re.IGNORECASE)
nic_bot_name_pattern = re.compile(r"\b(nic|nick|nicole|nikky|nik)\b", re.IGNORECASE)

# Initialize the database
db = SQLiteDB()
db.create_table()


class WhisperClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def get_text(self, audio_file_path: str) -> str:
        url = f"http://127.0.0.1:8080/inference"
        headers = {
            "accept": "application/json",
        }
        try:
            form = aiohttp.FormData()
            with open(audio_file_path, "rb") as f:
                form.add_field(
                    "file",
                    f,
                    filename=os.path.basename(audio_file_path),
                    content_type="audio/wav",
                )
                async with self._session.post(url, headers=headers, data=form) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("text", "")
                    logger.info(f"Error: {response.status} - {await response.text()}")
                    return ""
        except asyncio.TimeoutError:
            logger.info("Request timed out.")
            return ""
        except Exception as e:
            logger.info(f"Exception during API call: {e}")
            import traceback

            traceback.print_exc()
            return ""


class WhisperWorker:
    async def process_audio(self):
        """Process audio paths from the Redis queue."""
        # Connect to Redis
        logger.info(f"Connecting to Redis")
        if not redis_client.ping():
            raise ConnectionError("Failed to connect to Redis.")
        logger.info("Connected to Redis successfully.")
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            whisper_client = WhisperClient(session)
            while True:
                try:
                    # Block in a worker thread so we don't block the asyncio loop.
                    path_data = await asyncio.to_thread(
                        redis_client.blpop, WHISPER_QUEUE, 30
                    )
                    if not path_data or len(path_data) < 2:
                        continue

                    key, raw_value = path_data
                    logger.info(f"Received key: {key}")

                    try:
                        path_info = json.loads(raw_value)
                    except json.JSONDecodeError as e:
                        logger.info(f"Failed to decode JSON from Redis: {e}")
                        continue

                    user_id = path_info.get("user_id")
                    audio_path = path_info.get("audio_path")
                    trace_id = path_info.get("trace_id")
                    guild_id = path_info.get("guild_id")
                    channel_id = path_info.get("channel_id")

                    if not user_id or not audio_path:
                        logger.info("No valid user_id or audio_path in the received data.")
                        continue

                    logger.info(
                        f"Processing audio for user_id={user_id} trace_id={trace_id} guild_id={guild_id} channel_id={channel_id}"
                    )

                    text_response = await whisper_client.get_text(audio_path)
                    if not text_response:
                        logger.info("No text response received.")
                        # Keep the audio file for debugging if whisper returns nothing
                        continue

                    text_response = text_response.strip()
                    db.insert_entry(user_id, text_response)

                    payload = {
                        "unique_id": str(randint(100000, 999999)),
                        "message": text_response,
                    }
                    # Carry metadata forward for multi-guild routing later.
                    if trace_id:
                        payload["trace_id"] = trace_id
                    if guild_id:
                        payload["guild_id"] = guild_id
                    if channel_id:
                        payload["channel_id"] = channel_id
                    payload["user_id"] = user_id

                    if bot_name_pattern.search(text_response):
                        redis_client.lpush(VOICE_RESPONSE_QUEUE, json.dumps(payload))
                        logger.info("Pushed response to voice_response_queue")
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                    elif nic_bot_name_pattern.search(text_response):
                        redis_client.lpush(VOICE_NIC_RESPONSE_QUEUE, json.dumps(payload))
                        logger.info("Pushed response to voice_nic_response_queue")
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                    else:
                        logger.info(f"No bot name found in transcript: {text_response}")
                        # Keep audio file if we didn't route it.

                except Exception as e:
                    logger.info(f"Exception during processing: {e}")


def main():
    worker = WhisperWorker()
    asyncio.run(worker.process_audio())


if __name__ == "__main__":
    main()
