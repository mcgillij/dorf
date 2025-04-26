import asyncio
import json
import traceback
import logging

from bot.constants import (
    DERF_RESPONSE_KEY,
    DERF_RESPONSE_KEY_PREFIX,
    NIC_RESPONSE_KEY,
    NIC_RESPONSE_KEY_PREFIX,
)
from bot.redis_client import redis_client

logger = logging.getLogger(__name__)


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
            response = await bot.llm.get_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"{response_key_prefix}:{unique_id}", response)
        except Exception as e:
            logger.error(f"{queue_name}: Error processing response queue: {e}")
            traceback.print_exc()


async def process_derf_response_queue(bot):
    await process_response_queue(DERF_RESPONSE_KEY_PREFIX, DERF_RESPONSE_KEY, bot)


async def process_nic_response_queue(bot):
    await process_response_queue(NIC_RESPONSE_KEY_PREFIX, NIC_RESPONSE_KEY, bot)
