import os
import json
from utilities import WhisperClient
import redis
import asyncio  # Ensure you have this imported for running async code
from dotenv import load_dotenv

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

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
                path_data = redis_client.blpop('whisper_queue', timeout=30)  # Timeout of 30 seconds
                if not path_data or len(path_data) < 2:
                    print("No data received from Redis queue.")
                    continue
                key, raw_value = path_data  # Unpack the tuple correctly
                print(f"Received key: {key}, Raw Value: {raw_value}")
                # Extract the audio_path and user_id from the Redis value
                try:
                    path_info = json.loads(raw_value)
                    user_id = path_info.get('user_id')
                    audio_path = path_info.get('audio_path')
                    if not user_id or not audio_path:
                        print("No valid user_id or audio_path in the received data.")
                        continue
                    print(f"Processing {audio_path} for user_id: {user_id}...")
                    # Ensure WhisperClient.get_text is called within an event loop context
                    text_response = asyncio.run(self._get_text_from_audio(audio_path))
                    if text_response:
                        print(f"Text response: {text_response}")
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
    asyncio.run(worker.process_audio())  # Run the process_audio method within an event loop

if __name__ == "__main__":
    main()

