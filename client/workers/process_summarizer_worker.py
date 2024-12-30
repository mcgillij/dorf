import asyncio
import redis
import json
import traceback
from bot.utilities import derf_bot

redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)

async def process_summarizer_queue():
    """
    Continuously process requests for get_summarizer_response from Redis queue.
    """
    while True:
        try:
            task_data = redis_client.rpop('summarizer_queue')
            if not task_data:
                await asyncio.sleep(1)
                continue

            # Parse task data
            task = json.loads(task_data)
            unique_id = task['unique_id']
            message = task['message']

            # Call get_summarizer_response
            response = await derf_bot.get_summarizer_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"summarizer:{unique_id}", response)
        except Exception as e:
            print(f"Error processing summarizer queue: {e}")
            traceback.print_exc()

