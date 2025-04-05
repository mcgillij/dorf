from discord.ext import tasks
import os
import json
import asyncio
import redis
from bot.commands import bot, nic_bot
from bot.log_config import setup_logger

from dotenv import load_dotenv

from bot.utilities import split_message, derf_bot, nicole_bot
from bot.processing import (
    poll_redis_for_key,
    LONG_RESPONSE_THRESHOLD,
    process_audio_queue,
    process_nic_audio_queue,
)

logger = setup_logger(__name__)

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", ""))


@tasks.loop(seconds=1)
async def monitor_nic_response_queue():
    """Monitor the Redis voice response queue for new items."""

    logger.info("Nic: Monitoring voice response queue...")
    while True:
        try:
            # Fetch an item from the Redis response queue
            queued_item = redis_client.rpop("voice_nic_response_queue")
            if queued_item is None:
                await asyncio.sleep(1)  # No items in the queue, wait and retry
                continue

            logger.info(f"Nic: Received voice queued item: {queued_item}")

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
            channel = nic_bot.get_channel(fallback_channel_id)
            if not channel:
                logger.info(f"Nic: Channel {fallback_channel_id} not found.")
                continue
            guild = channel.guild
            # send the question the user asked to back to the chat before processing response
            for message_chunk in split_message(actual_message, 2000):
                await channel.send(f"{guild.get_member(user_id)}: {message_chunk}")

            # Call get_response
            response_from_llm = await nicole_bot.get_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"response:{unique_id}", response_from_llm)

            # Poll Redis for the response
            response = await poll_redis_for_key(f"response:{unique_id}")

            # Send the response in chunks
            for response_chunk in split_message(response, 2000):
                await channel.send(response_chunk)

            # Check for voice channel users
            human_in_voice_channel = bool(
                ctx.guild.voice_client
                and any(
                    not member.bot for member in ctx.guild.voice_client.channel.members
                )
            )
            logger.info(f"Nic: human in voice channel: {human_in_voice_channel}")

            # Summarize response if it's long
            if len(response) > LONG_RESPONSE_THRESHOLD:
                redis_client.lpush(
                    "summarizer_nic_queue",
                    json.dumps({"unique_id": unique_id, "message": response}),
                )
                summary_response = await poll_redis_for_key(f"summarizer:{unique_id}")

                await channel.send(summary_response)
                if human_in_voice_channel:
                    await process_nic_audio_queue(unique_id, [summary_response])
            else:
                if human_in_voice_channel:
                    await process_nic_audio_queue(unique_id, [response])

        except Exception as e:
            logger.error(f"Nic: Error in monitor_response_queue: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors


@tasks.loop(seconds=1)
async def monitor_response_queue():
    """Monitor the Redis voice response queue for new items."""

    logger.info("Monitoring voice response queue...")
    while True:
        try:
            # Fetch an item from the Redis response queue
            queued_item = redis_client.rpop("voice_response_queue")
            if queued_item is None:
                await asyncio.sleep(1)  # No items in the queue, wait and retry
                continue

            logger.info(f"Received voice queued item: {queued_item}")

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
            channel = bot.get_channel(fallback_channel_id)
            if not channel:
                logger.info(f"Channel {fallback_channel_id} not found.")
                continue
            guild = channel.guild
            # send the question the user asked to back to the chat before processing response
            for message_chunk in split_message(actual_message, 2000):
                await channel.send(f"{guild.get_member(user_id)}: {message_chunk}")

            # Call get_response
            response_from_llm = await derf_bot.get_response(message)

            # Store the response in Redis for retrieval
            redis_client.set(f"response:{unique_id}", response_from_llm)

            # Poll Redis for the response
            response = await poll_redis_for_key(f"response:{unique_id}")

            # Send the response in chunks
            for response_chunk in split_message(response, 2000):
                await channel.send(response_chunk)

            # Check for voice channel users
            human_in_voice_channel = bool(
                ctx.guild.voice_client
                and any(
                    not member.bot for member in ctx.guild.voice_client.channel.members
                )
            )
            logger.info(f"Human in voice channel: {human_in_voice_channel}")

            # Summarize response if it's long
            if len(response) > LONG_RESPONSE_THRESHOLD:
                redis_client.lpush(
                    "summarizer_queue",
                    json.dumps({"unique_id": unique_id, "message": response}),
                )
                summary_response = await poll_redis_for_key(f"summarizer:{unique_id}")

                await channel.send(summary_response)
                if human_in_voice_channel:
                    await process_audio_queue(unique_id, [summary_response])
            else:
                if human_in_voice_channel:
                    await process_audio_queue(unique_id, [response])

        except Exception as e:
            logger.error(f"Error in monitor_response_queue: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors
