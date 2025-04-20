import asyncio
import redis
import json
import traceback

from bot.utilities import derf_bot, nicole_bot, replace_userids_with_username
from bot.log_config import setup_logger

logger = setup_logger(__name__)

# Initialize Redis client
redis_client = redis.Redis(host="0.0.0.0", port=6379, decode_responses=True)


async def process_response_queue():
    """
    Continuously process requests for get_response from Redis queue.
    """
    while True:
        try:
            task_data = redis_client.rpop("response_queue")
            if not task_data:
                await asyncio.sleep(1)
                continue
            logger.info(f"Received task data: {task_data}")

            # Parse task data
            task = json.loads(task_data)
            unique_id = task["unique_id"]
            message = task["message"]

            # Call get_response
            response = await derf_bot.get_response(message)
            response = await replace_userids_with_username(response)

            # Store the response in Redis for retrieval
            redis_client.set(f"response:{unique_id}", response)
        except Exception as e:
            logger.error(f"Error processing response queue: {e}")
            traceback.print_exc()


async def process_nic_response_queue():
    """
    Continuously process requests for get_response from Redis queue.
    """
    while True:
        try:
            task_data = redis_client.rpop("response_nic_queue")
            if not task_data:
                await asyncio.sleep(1)
                continue
            logger.info(f"Nic: Received task data: {task_data}")

            # Parse task data
            task = json.loads(task_data)
            unique_id = task["unique_id"]
            message = task["message"]

            # Call get_response
            response = await nicole_bot.get_response(message)
            response = await replace_userids_with_username(response)

            # Store the response in Redis for retrieval
            redis_client.set(f"response_nic:{unique_id}", response)
        except Exception as e:
            logger.error(f"Nic: Error processing response queue: {e}")
            traceback.print_exc()
