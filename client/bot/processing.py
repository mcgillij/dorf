import json
import logging

from bot.utilities import (
    split_message,
    generate_unique_id,
    poll_redis_for_key,
    replace_userids_with_username,
)
from bot.redis_client import redis_client
from bot.constants import (
    LONG_RESPONSE_THRESHOLD,
    DERF_SUMMARIZER_QUEUE,
    NIC_SUMMARIZER_QUEUE,
    DERF_RESPONSE_KEY_PREFIX,
    NIC_RESPONSE_KEY_PREFIX,
    DERF_RESPONSE_KEY,
    NIC_RESPONSE_KEY,
    DERF_AUDIO_QUEUE,
    NIC_AUDIO_QUEUE,
)

logger = logging.getLogger(__name__)

# Context dictionary
context_dict = {}


# Generalized function to queue message processing
async def queue_message_processing(ctx, message: str, queue_name: str):
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"{queue_name.capitalize()}: Unique ID: {unique_id}")
    # Store the context if not already stored
    context_dict.setdefault(unique_id, ctx)
    # Queue the message for processing
    message = await replace_userids_with_username(message)
    redis_client.lpush(
        queue_name,
        json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}),
    )
    return unique_id


# Wrappers for specific queues
async def queue_derf_message_processing(ctx, message: str):
    return await queue_message_processing(ctx, message, DERF_RESPONSE_KEY_PREFIX)


async def queue_nic_message_processing(ctx, message: str):
    return await queue_message_processing(ctx, message, NIC_RESPONSE_KEY_PREFIX)


# Generalized function to process and send responses
async def process_response(
    ctx,
    unique_id: str,
    response_key_prefix: str,
    summarizer_queue: str,
    audio_queue_func,
):
    # Poll Redis for the result
    key = f"{response_key_prefix}:{unique_id}"
    response = await poll_redis_for_key(key)
    logger.debug(f"{response_key_prefix.capitalize()}: Response: {response}")
    response = await replace_userids_with_username(response)
    logger.debug(
        f"{response_key_prefix.capitalize()}: Response after replacing userids: {response}"
    )
    # Send the response in chunks
    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)
    # Check for voice channel users
    human_in_voice_channel = bool(
        ctx.guild.voice_client
        and any(not member.bot for member in ctx.guild.voice_client.channel.members)
    )
    logger.info(
        f"{response_key_prefix.capitalize()}: Are there users in voice chat?: {human_in_voice_channel}"
    )
    # Summarize response if it's long
    if len(response) > LONG_RESPONSE_THRESHOLD:
        redis_client.lpush(
            summarizer_queue,
            json.dumps({"unique_id": unique_id, "message": response}),
        )
        summary_key = f"{summarizer_queue}:{unique_id}"
        summary_response = await poll_redis_for_key(summary_key)
        await ctx.send(summary_response)
        if human_in_voice_channel:
            await audio_queue_func(unique_id, [summary_response])
    else:
        if human_in_voice_channel:
            await audio_queue_func(unique_id, [response])


# Wrappers for specific response processing
async def process_derf_response(ctx, unique_id: str):
    await process_response(
        ctx,
        unique_id,
        DERF_RESPONSE_KEY,
        DERF_SUMMARIZER_QUEUE,
        process_derf_audio_queue,
    )


async def process_nic_response(ctx, unique_id: str):
    await process_response(
        ctx,
        unique_id,
        NIC_RESPONSE_KEY,
        NIC_SUMMARIZER_QUEUE,
        process_nic_audio_queue,
    )


# Generalized function to process audio queue
async def process_audio_queue(unique_id: str, messages: list[str], queue_name: str):
    """Queues messages for audio generation if users are in the voice channel."""
    index = 1
    for msg in messages:
        redis_client.lpush(queue_name, f"{unique_id}|{index}|{msg}")
        index += 1


# Wrappers for specific audio queues
async def process_derf_audio_queue(unique_id: str, messages: list[str]):
    await process_audio_queue(unique_id, messages, DERF_AUDIO_QUEUE)


async def process_nic_audio_queue(unique_id: str, messages: list[str]):
    await process_audio_queue(unique_id, messages, NIC_AUDIO_QUEUE)
