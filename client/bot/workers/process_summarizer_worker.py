import asyncio
import json
import traceback
import logging
from bot.constants import (
    DERF_SUMMARIZER_QUEUE,
    NIC_SUMMARIZER_QUEUE,
    SUMMARIZER_RESPONSE_KEY,
)
from bot.redis_client import redis_client

logger = logging.getLogger(__name__)


async def process_queue(queue_name, response_key_prefix, bot):
    """
    Generic function to process requests from a Redis queue.
    """
    while True:
        try:
            task_data = redis_client.rpop(queue_name)
            if not task_data:
                await asyncio.sleep(1)
                continue
            logger.info(f"{queue_name} item found, processing")
            # Parse task data
            task = json.loads(task_data)
            unique_id = task["unique_id"]
            message = task["message"]

            # Call the bot's get_summarizer_response
            response = await bot.llm.get_summarizer_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"{response_key_prefix}:{unique_id}", response)
        except Exception as e:
            logger.error(f"Error processing {queue_name}: {e}")
            traceback.print_exc()


async def process_derf_summarizer_queue(bot):
    """
    Wrapper for processing the summarizer queue using derf_bot.
    """
    await process_queue(DERF_SUMMARIZER_QUEUE, SUMMARIZER_RESPONSE_KEY, bot)


async def process_nic_summarizer_queue(bot):
    """
    Wrapper for processing the nic_summarizer queue using nicole_bot.
    """
    await process_queue(NIC_SUMMARIZER_QUEUE, SUMMARIZER_RESPONSE_KEY, bot)
