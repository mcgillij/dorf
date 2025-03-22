import os
import re
import json
from random import randint
import redis
import asyncio  # Ensure you have this imported for running async code
from dotenv import load_dotenv
import aiohttp
from db import SQLiteDB
import logging

logger = logging.getLogger(__name__)
FORMAT = "%(asctime)s - %(message)s"
logging.basicConfig(format=FORMAT)
logger.addHandler(logging.FileHandler("derf.log"))
logger.setLevel(logging.DEBUG)

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

bot_name_pattern = re.compile(r"\b(bot|derf|derfbot|dorf|dwarf)\b", re.IGNORECASE)
nic_bot_name_pattern = re.compile(r"\b(nic|nick|nicole|nikky|nik)\b", re.IGNORECASE)

# Initialize the database
db = SQLiteDB()
db.create_table()  # Create table if it doesn't exist


class WhisperClient:
    async def get_text(self, audio_file_path: str) -> str:
        url = f"http://127.0.0.1:8080/inference"
        headers = {
            "accept": "application/json",
        }
        files = {"file": open(audio_file_path, "rb")}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, data=files) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        return json_response.get("text", "")
                    else:
                        logger.info(
                            f"Error: {response.status} - {await response.text()}"
                        )
                        return ""
            except asyncio.TimeoutError:
                logger.info("Request timed out.")
                return "The whisper request timed out. Please try again later."
            except Exception as e:
                logger.info(f"Exception during API call: {e}")
                traceback.print_exc()
                return "An error occurred while processing the request. Please try again later."


class WhisperWorker:
    def process_audio(self):
        """Process audio paths from the Redis queue."""
        # Connect to Redis
        logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
        if not redis_client.ping():
            raise ConnectionError("Failed to connect to Redis.")
        logger.info("Connected to Redis successfully.")
        while True:
            try:
                # Get a blocking pop from the queue (blocking until an item is available)
                path_data = redis_client.blpop(
                    "whisper_queue", timeout=30
                )  # Timeout of 30 seconds
                if not path_data or len(path_data) < 2:
                    continue
                key, raw_value = path_data  # Unpack the tuple correctly
                logger.info(f"Received key: {key}, Raw Value: {raw_value}")
                # Extract the audio_path and user_id from the Redis value
                try:
                    path_info = json.loads(raw_value)
                    user_id = path_info.get("user_id")
                    audio_path = path_info.get("audio_path")
                    if not user_id or not audio_path:
                        logger.info(
                            "No valid user_id or audio_path in the received data."
                        )
                        continue
                    logger.info(f"Processing {audio_path} for user_id: {user_id}...")
                    # Ensure WhisperClient.get_text is called within an event loop context
                    text_response = asyncio.run(self._get_text_from_audio(audio_path))
                    if text_response:
                        logger.debug(f"{user_id}: {text_response}")
                        if bot_name_pattern.search(text_response):
                            logger.info(f"replying_to: {text_response}")
                            unique_id = randint(
                                100000, 999999
                            )  # Generate a random unique ID
                            redis_client.lpush(
                                "voice_response_queue",
                                json.dumps(
                                    {
                                        "unique_id": str(unique_id),
                                        "message": f"{user_id}: {text_response.strip()}",
                                    }
                                ),
                            )
                            logger.info(f"Pushed response to Redis queue.")
                            # now we can remove the audio file
                        elif nic_bot_name_pattern.search(text_response):
                            logger.info(f"replying_to: {text_response}")
                            unique_id = randint(
                                100000, 999999
                            )  # Generate a random unique ID
                            redis_client.lpush(
                                "voice_nic_response_queue",
                                json.dumps(
                                    {
                                        "unique_id": str(unique_id),
                                        "message": f"{user_id}: {text_response.strip()}",
                                    }
                                ),
                            )
                            logger.info(f"Pushed response to Redis queue.")
                            # now we can remove the audio file
                        else:
                            logger.info(
                                f"No bot name found in text response: {text_response}"
                            )
                        os.remove(audio_path)
                        db.insert_entry(user_id, text_response.strip())

                    else:
                        logger.info("No text response received.")
                except json.JSONDecodeError as e:
                    logger.info(f"Failed to decode JSON from Redis: {e}")
            except redis.exceptions.TimeoutError:
                logger.info("Redis queue timeout occurred.")
            except Exception as e:
                logger.info(f"Exception during processing: {e}")

    async def _get_text_from_audio(self, audio_path):
        """Get text from the given audio path using WhisperClient."""
        whisper_client = WhisperClient()
        return await whisper_client.get_text(audio_path)


def main():
    worker = WhisperWorker()
    asyncio.run(
        worker.process_audio(),
    )  # Run the process_audio method within an event loop


if __name__ == "__main__":
    main()
