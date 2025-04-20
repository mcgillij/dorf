from discord.ext import tasks
import os
import json
import asyncio
import redis
from bot.commands import bot, nic_bot
from bot.log_config import setup_logger

from dotenv import load_dotenv

from bot.utilities import split_message
from bot.processing import (
    poll_redis_for_key,
    LONG_RESPONSE_THRESHOLD,
)

logger = setup_logger(__name__)

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", ""))

from discord.ext import tasks


async def monitor_response_queue(
    bot_instance,
    bot_logic,
    queue_name,
    response_key_prefix,
    summarizer_queue,
    process_audio_function,
    logger,
):
    await bot_instance.wait_until_ready()
    logger.info(f"Started monitoring {queue_name}...")
    while True:
        try:
            queued_item = redis_client.rpop(queue_name)
            if queued_item is None:
                await asyncio.sleep(1)
                continue

            logger.info(f"Received queued item: {queued_item}")

            data = json.loads(queued_item)
            unique_id = data["unique_id"]
            message = data["message"]

            user_id, actual_message = message.split(":", 1)
            user_id = int(user_id)

            fallback_channel_id = int(os.getenv("CHAT_CHANNEL_ID", ""))
            channel = bot_instance.get_channel(fallback_channel_id)
            if not channel:
                logger.warning(f"Channel {fallback_channel_id} not found.")
                continue

            guild = channel.guild
            for message_chunk in split_message(actual_message, 2000):
                await channel.send(f"{guild.get_member(user_id)}: {message_chunk}")

            # Get response
            response_from_llm = await bot_logic.get_response(message)
            redis_client.set(f"{response_key_prefix}:{unique_id}", response_from_llm)

            response = await poll_redis_for_key(f"{response_key_prefix}:{unique_id}")
            for response_chunk in split_message(response, 2000):
                await channel.send(response_chunk)

            voice_client = channel.guild.voice_client
            human_in_voice_channel = (
                voice_client is not None
                and voice_client.channel is not None
                and any(not m.bot for m in voice_client.channel.members)
            )

            if len(response) > 2000:  # or use your LONG_RESPONSE_THRESHOLD
                redis_client.lpush(
                    summarizer_queue,
                    json.dumps({"unique_id": unique_id, "message": response}),
                )
                summary_response = await poll_redis_for_key(f"summarizer:{unique_id}")
                await channel.send(summary_response)
                if human_in_voice_channel:
                    await process_audio_function(unique_id, [summary_response])
            else:
                if human_in_voice_channel:
                    await process_audio_function(unique_id, [response])

        except Exception as e:
            logger.error(f"Error in monitor_response_queue: {e}")
            await asyncio.sleep(1)


def start_monitor_task(
    bot_instance,
    bot_logic,
    queue_name,
    response_key_prefix,
    summarizer_queue,
    process_audio_function,
    logger_name,
):
    import logging

    logger = logging.getLogger(logger_name)
    return asyncio.create_task(
        monitor_response_queue(
            bot_instance,
            bot_logic,
            queue_name,
            response_key_prefix,
            summarizer_queue,
            process_audio_function,
            logger,
        )
    )
