import os
import re
import json
from random import randint
import redis
import asyncio  # Ensure you have this imported for running async code
from dotenv import load_dotenv
import aiohttp

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

bot_name_pattern = re.compile(r"\b(bot|derf|derfbot|dorf|dwarf)\b", re.IGNORECASE)


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
                        print(f"Error: {response.status} - {await response.text()}")
                        return ""
            except asyncio.TimeoutError:
                print("Request timed out.")
                return "The whisper request timed out. Please try again later."
            except Exception as e:
                print(f"Exception during API call: {e}")
                traceback.print_exc()
                return "An error occurred while processing the request. Please try again later."


class WhisperWorker:
    def process_audio(self):
        """Process audio paths from the Redis queue."""
        # Connect to Redis
        print(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
        if not redis_client.ping():
            raise ConnectionError("Failed to connect to Redis.")
        print("Connected to Redis successfully.")
        while True:
            try:
                # Get a blocking pop from the queue (blocking until an item is available)
                path_data = redis_client.blpop(
                    "whisper_queue", timeout=30
                )  # Timeout of 30 seconds
                if not path_data or len(path_data) < 2:
                    # print("No data received from Redis queue.")
                    continue
                key, raw_value = path_data  # Unpack the tuple correctly
                print(f"Received key: {key}, Raw Value: {raw_value}")
                # Extract the audio_path and user_id from the Redis value
                try:
                    path_info = json.loads(raw_value)
                    user_id = path_info.get("user_id")
                    audio_path = path_info.get("audio_path")
                    if not user_id or not audio_path:
                        print("No valid user_id or audio_path in the received data.")
                        continue
                    print(f"Processing {audio_path} for user_id: {user_id}...")
                    # Ensure WhisperClient.get_text is called within an event loop context
                    text_response = asyncio.run(self._get_text_from_audio(audio_path))
                    if text_response:
                        if bot_name_pattern.search(text_response):
                            # {"unique_id": 465152, "message": "427590626905948165: What's your favorite weapon?\n"}
                            # {"unique_id": "3a946d5e5d11350474220da38d878e38", "message": "427590626905948165:whats your favorite weapon"}
                            print(f"Text response: {text_response}")
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
                            print(f"Pushed response to Redis queue.")
                            # now we can remove the audio file
                        else:
                            print(
                                f"No bot name found in text response: {text_response}"
                            )
                        os.remove(audio_path)

                    else:
                        print("No text response received.")
                except json.JSONDecodeError as e:
                    print(f"Failed to decode JSON from Redis: {e}")
            except redis.exceptions.TimeoutError:
                print("Redis queue timeout occurred.")
            except Exception as e:
                print(f"Exception during processing: {e}")

    async def _get_text_from_audio(self, audio_path):
        """Get text from the given audio path using WhisperClient."""
        whisper_client = WhisperClient()
        return await whisper_client.get_text(audio_path)


# Example usage in a synchronous context
def main():
    worker = WhisperWorker()
    asyncio.run(
        worker.process_audio()
    )  # Run the process_audio method within an event loop


if __name__ == "__main__":
    main()
