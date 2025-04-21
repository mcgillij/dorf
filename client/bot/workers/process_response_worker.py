import asyncio
import redis
import json
import traceback

from bot.utilities import replace_userids_with_username
from bot.log_config import setup_logger
from bot.commands import bot, nic_bot

logger = setup_logger(__name__)

# Initialize Redis client
redis_client = redis.Redis(host="0.0.0.0", port=6379, decode_responses=True)


async def process_response_queue(queue_name, response_key_prefix, bot):
    """
    Continuously process requests for get_response from a specified Redis queue.
    """
    while True:
        try:
            task_data = redis_client.rpop(queue_name)
            if not task_data:
                await asyncio.sleep(1)
                continue
            logger.info(f"{queue_name}: Received task data: {task_data}")

            # Parse task data
            task = json.loads(task_data)
            unique_id = task["unique_id"]
            message = task["message"]

            # Call get_response
            response = await bot.get_response(message)
            response = await replace_userids_with_username(response)

            # Store the response in Redis for retrieval
            redis_client.set(f"{response_key_prefix}:{unique_id}", response)
        except Exception as e:
            logger.error(f"{queue_name}: Error processing response queue: {e}")
            traceback.print_exc()


async def process_derf_response_queue():
    await process_response_queue("response_queue", "response", bot.llm)


async def process_nic_response_queue():
    await process_response_queue("response_nic_queue", "response_nic", nic_bot.llm)
