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


async def queue_message_processing(ctx, message: str, bot_type: str = "derf") -> str:
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"{bot_type.capitalize()}: Unique ID: {unique_id}")
    context_dict.setdefault(unique_id, ctx)
    message = await replace_userids_with_username(message)

    queue_name = "response_queue" if bot_type == "derf" else "response_nic_queue"
    redis_client.lpush(
        queue_name,
        json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}),
    )
    return unique_id


async def process_response(ctx, unique_id: str, bot_type: str = "derf"):
    key = f"response{'' if bot_type == 'derf' else '_nic'}:{unique_id}"
    response = await poll_redis_for_key(key)
    response = await replace_userids_with_username(response)
    logger.debug(
        f"{bot_type.capitalize()}: Response after replacing userids: {response}"
    )

    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)

    human_in_voice_channel = bool(
        ctx.guild.voice_client
        and any(not member.bot for member in ctx.guild.voice_client.channel.members)
    )
    logger.info(
        f"{bot_type.capitalize()}: Are there users in voice chat?: {human_in_voice_channel}"
    )

    if len(response) > LONG_RESPONSE_THRESHOLD:
        summarizer_queue = f"summarizer{'' if bot_type == 'derf' else '_nic'}_queue"
        redis_client.lpush(
            summarizer_queue,
            json.dumps({"unique_id": unique_id, "message": response}),
        )
        summary_key = f"summarizer{'' if bot_type == 'derf' else '_nic'}:{unique_id}"
        summary_response = await poll_redis_for_key(summary_key)
        await ctx.send(summary_response)
        if human_in_voice_channel:
            await process_audio_queue(unique_id, [summary_response], bot_type)
    else:
        if human_in_voice_channel:
            await process_audio_queue(unique_id, [response], bot_type)


async def process_audio_queue(
    unique_id: str, messages: list[str], bot_type: str = "derf"
):
    audio_queue = "audio_queue" if bot_type == "derf" else "audio_nic_queue"
    for index, msg in enumerate(messages, start=1):
        redis_client.lpush(audio_queue, f"{unique_id}|{index}|{msg}")


# wrapper functions for derf and nic bots
async def queue_derf(ctx, message: str):
    return await queue_message_processing(ctx, message, bot_type="derf")


async def process_derf(ctx, unique_id: str):
    await process_response(ctx, unique_id, bot_type="derf")


# For nic bot
async def queue_nic(ctx, message: str):
    return await queue_message_processing(ctx, message, bot_type="nic")


async def process_nic(ctx, unique_id: str):
    await process_response(ctx, unique_id, bot_type="nic")
