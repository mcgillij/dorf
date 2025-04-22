import asyncio
import redis
import json
import traceback
from bot.client import derf_bot, nic_bot
import logging

logger = logging.getLogger(__name__)

redis_client = redis.Redis(host="0.0.0.0", port=6379, decode_responses=True)


async def process_queue(queue_name, bot, response_key_prefix):
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
            response = await bot.get_summarizer_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"{response_key_prefix}:{unique_id}", response)
        except Exception as e:
            logger.error(f"Error processing {queue_name}: {e}")
            traceback.print_exc()


async def process_derf_summarizer_queue():
    """
    Wrapper for processing the summarizer queue using derf_bot.
    """
    await process_queue("summarizer_queue", derf_bot.llm, "summarizer")


async def process_nic_summarizer_queue():
    """
    Wrapper for processing the nic_summarizer queue using nicole_bot.
    """
    await process_queue("nic_summarizer_queue", nic_bot.llm, "summarizer")
