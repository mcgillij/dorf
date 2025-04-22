from discord.ext import tasks
import os
import json
import asyncio
import redis
import logging

from dotenv import load_dotenv

from bot.utilities import split_message
from bot.client import derf_bot, nic_bot
from bot.processing import (
    poll_redis_for_key,
    LONG_RESPONSE_THRESHOLD,
    process_derf_audio_queue,
    process_nic_audio_queue,
)

logger = logging.getLogger(__name__)

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", ""))


async def process_response_queue(
    queue_name, bot_instance, process_audio_func, summarizer_queue_name
):
    """Generic function to process a Redis response queue."""
    logger.info(f"Monitoring {queue_name}...")
    while True:
        try:
            # Fetch an item from the Redis response queue
            queued_item = redis_client.rpop(queue_name)
            if queued_item is None:
                await asyncio.sleep(1)  # No items in the queue, wait and retry
                continue

            logger.info(f"Received queued item from {queue_name}: {queued_item}")

            # Parse the queued item
            data = json.loads(queued_item)
            unique_id = data["unique_id"]
            message = data["message"]

            # Extract the channel ID and user ID from the message
            user_id, actual_message = message.split(":", 1)
            user_id = int(user_id)

            # Define a fallback channel ID for automated responses
            fallback_channel_id = CHAT_CHANNEL_ID

            # Fetch the channel
            channel = bot_instance.get_channel(fallback_channel_id)
            if not channel:
                logger.info(f"Channel {fallback_channel_id} not found.")
                continue
            guild = channel.guild

            # Send the question the user asked back to the chat before processing response
            for message_chunk in split_message(actual_message, 2000):
                await channel.send(f"{guild.get_member(user_id)}: {message_chunk}")

            # Call get_response
            response_from_llm = await bot_instance.llm.get_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"response:{unique_id}", response_from_llm)

            # Poll Redis for the response
            response = await poll_redis_for_key(f"response:{unique_id}")

            # Send the response in chunks
            for response_chunk in split_message(response, 2000):
                await channel.send(response_chunk)

            # Check for voice channel users
            voice_client = channel.guild.voice_client
            human_in_voice_channel = (
                voice_client is not None
                and voice_client.channel is not None
                and any(not m.bot for m in voice_client.channel.members)
            )
            logger.info(f"Human in voice channel: {human_in_voice_channel}")

            # Summarize response if it's long
            if len(response) > LONG_RESPONSE_THRESHOLD:
                redis_client.lpush(
                    summarizer_queue_name,
                    json.dumps({"unique_id": unique_id, "message": response}),
                )
                summary_response = await poll_redis_for_key(f"summarizer:{unique_id}")

                await channel.send(summary_response)
                if human_in_voice_channel:
                    await process_audio_func(unique_id, [summary_response])
            else:
                if human_in_voice_channel:
                    await process_audio_func(unique_id, [response])

        except Exception as e:
            logger.error(f"Error in processing {queue_name}: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors


@tasks.loop(seconds=1)
async def monitor_nic_response_queue():
    """Monitor the Redis voice response queue for Nic."""
    await process_response_queue(
        "voice_nic_response_queue",
        nic_bot,
        process_nic_audio_queue,
        "summarizer_nic_queue",
    )


@tasks.loop(seconds=1)
async def monitor_derf_response_queue():
    """Monitor the Redis voice response queue."""
    await process_response_queue(
        "voice_response_queue",
        derf_bot,
        process_derf_audio_queue,
        "summarizer_queue",
    )
