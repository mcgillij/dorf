import json
import os

import redis
from dotenv import load_dotenv

from bot.utilities import (
    split_message,
    generate_unique_id,
    poll_redis_for_key,
    replace_userids_with_username,
)
from bot.log_config import setup_logger

logger = setup_logger(__name__)

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Context dictionary
context_dict = {}

LONG_RESPONSE_THRESHOLD = 1000


# Function to queue message processing
async def queue_message_processing(ctx, message: str):
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"Unique ID: {unique_id}")
    # Store the context if not already stored
    context_dict.setdefault(unique_id, ctx)
    # Queue the message for processing
    message = await replace_userids_with_username(message)
    redis_client.lpush(
        "response_queue",
        json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}),
    )
    return unique_id


async def queue_nic_message_processing(ctx, message: str):
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"Nic: Unique ID: {unique_id}")
    # Store the context if not already stored
    context_dict.setdefault(unique_id, ctx)
    message = await replace_userids_with_username(message)
    # Queue the message for processing
    redis_client.lpush(
        "response_nic_queue",
        json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}),
    )
    return unique_id


# Function to process and send responses
async def process_response(ctx, unique_id: str):
    # Poll Redis for the result
    key = f"response:{unique_id}"
    response = await poll_redis_for_key(key)
    # Send the response in chunks
    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)
    # Check for voice channel users
    human_in_voice_channel = bool(
        ctx.guild.voice_client
        and any(not member.bot for member in ctx.guild.voice_client.channel.members)
    )
    logger.info(f"Are there users in voice chat?: {human_in_voice_channel}")
    # Summarize response if it's long
    if len(response) > LONG_RESPONSE_THRESHOLD:
        redis_client.lpush(
            "summarizer_queue",
            json.dumps({"unique_id": unique_id, "message": response}),
        )
        summary_key = f"summarizer:{unique_id}"
        summary_response = await poll_redis_for_key(summary_key)
        await ctx.send(summary_response)
        if human_in_voice_channel:
            await process_audio_queue(unique_id, [summary_response])
    else:
        if human_in_voice_channel:
            await process_audio_queue(unique_id, [response])


async def process_nic_response(ctx, unique_id: str):
    # Poll Redis for the result
    key = f"response_nic:{unique_id}"
    response = await poll_redis_for_key(key)
    logger.debug(f"Nic: Response: {response}")
    response = await replace_userids_with_username(response)
    logger.debug(f"Nic: Response after replacing userids: {response}")
    # Send the response in chunks
    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)
    # Check for voice channel users
    human_in_voice_channel = bool(
        ctx.guild.voice_client
        and any(not member.bot for member in ctx.guild.voice_client.channel.members)
    )
    logger.info(f"Nic: Are there users in voice chat?: {human_in_voice_channel}")
    # Summarize response if it's long
    if len(response) > LONG_RESPONSE_THRESHOLD:
        redis_client.lpush(
            "summarizer_nic_queue",
            json.dumps({"unique_id": unique_id, "message": response}),
        )
        summary_key = f"summarizer_nic:{unique_id}"
        summary_response = await poll_redis_for_key(summary_key)
        await ctx.send(summary_response)
        if human_in_voice_channel:
            await process_nic_audio_queue(unique_id, [summary_response])
    else:
        if human_in_voice_channel:
            await process_nic_audio_queue(unique_id, [response])


async def process_audio_queue(unique_id: str, messages: list[str]):
    """Queues messages for audio generation if users are in the voice channel."""
    index = 1
    for msg in messages:
        redis_client.lpush("audio_queue", f"{unique_id}|{index}|{msg}")
        index += 1


async def process_nic_audio_queue(unique_id: str, messages: list[str]):
    """Queues messages for audio generation if users are in the voice channel."""
    index = 1
    for msg in messages:
        redis_client.lpush("audio_nic_queue", f"{unique_id}|{index}|{msg}")
        index += 1
