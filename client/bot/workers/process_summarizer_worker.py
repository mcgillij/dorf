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
            task_data = await asyncio.to_thread(redis_client.rpop, queue_name)
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

            response = (response or "").strip()
            if not response:
                logger.error(
                    "summarizer.empty queue=%s unique_id=%s message_len=%s",
                    queue_name,
                    unique_id,
                    len(message or ""),
                )

            # Store the response in Redis for retrieval. TTL guards against
            # leaks when the waiter gave up (15s poll) before deleting the key.
            await asyncio.to_thread(
                redis_client.set,
                f"{response_key_prefix}:{unique_id}",
                response,
                ex=3600,
            )
        except Exception as e:
            logger.exception(f"Error processing {queue_name}: {e}")


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
