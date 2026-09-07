import json
import logging
import asyncio
import os
import uuid

from bot.utilities import (
    generate_unique_id,
    poll_redis_for_key_with_timeout,
    preprocess_mentions,
    postprocess_mentions,
)
from bot.redis_client import redis_client
from bot.pipelines.unified import RequestContext, deliver_existing_response
from bot.constants import (
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

# How long a text command waits for the response worker before giving up.
RESPONSE_WAIT_MAX_S = float(os.getenv("RESPONSE_WAIT_MAX_S", "120"))


# Generalized function to queue message processing
async def queue_message_processing(ctx, message: str, queue_name: str):
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"{queue_name.capitalize()}: Unique ID: {unique_id}")
    # Preprocess mentions
    message, mention_map = await preprocess_mentions(ctx, message)
    # Store mention map in Redis
    mention_map_key = f"mention_map:{unique_id}"
    await asyncio.to_thread(
        redis_client.set, mention_map_key, json.dumps(mention_map), ex=3600
    )  # 1 hour TTL
    logger.info(f"Here's the username: {ctx.author.name}")
    await asyncio.to_thread(
        redis_client.lpush,
        queue_name,
        json.dumps({"unique_id": unique_id, "message": f"{message}"}),
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
    # Poll Redis for the result (bounded: a dead response worker must not hang
    # the command forever).
    key = f"{response_key_prefix}:{unique_id}"
    response = await poll_redis_for_key_with_timeout(
        key, max_wait_s=RESPONSE_WAIT_MAX_S, delete=True
    )
    if response is None:
        logger.error(
            "%s: no response after %.0fs (response worker down?); key=%s",
            response_key_prefix,
            RESPONSE_WAIT_MAX_S,
            key,
        )
        await ctx.send("Something went wrong getting a response — try again in a bit.")
        return
    logger.debug(f"{response_key_prefix.capitalize()}: Response: {response}")
    if not response.strip():
        logger.warning(
            "%s: LLM returned an empty response; key=%s", response_key_prefix, key
        )
        await ctx.send("I couldn't come up with a response — try again in a bit.")
        return
    # Postprocess mentions
    mention_map_key = f"mention_map:{unique_id}"
    mention_map_raw = await asyncio.to_thread(redis_client.get, mention_map_key)
    if mention_map_raw:
        mention_map = json.loads(mention_map_raw)
        response = await postprocess_mentions(ctx, response, mention_map)
    logger.debug(
        f"{response_key_prefix.capitalize()}: Response after replacing userids: {response}"
    )
    # Check for voice channel users (ctx.guild is None in DMs)
    human_in_voice_channel = bool(
        ctx.guild
        and ctx.guild.voice_client
        and ctx.guild.voice_client.channel
        and any(not member.bot for member in ctx.guild.voice_client.channel.members)
    )
    logger.info(
        f"{response_key_prefix.capitalize()}: Are there users in voice chat?: {human_in_voice_channel}"
    )
    await deliver_existing_response(
        send=ctx.send,
        response_text=response,
        unique_id=str(unique_id),
        summarizer_queue_name=summarizer_queue,
        audio_queue_name=(
            DERF_AUDIO_QUEUE
            if response_key_prefix == DERF_RESPONSE_KEY
            else NIC_AUDIO_QUEUE
        ),
        human_in_voice_channel=human_in_voice_channel,
        ctx=RequestContext(
            trace_id=str(uuid.uuid4()),
            guild_id=getattr(ctx.guild, "id", None),
            channel_id=getattr(ctx.channel, "id", None),
            user_id=getattr(ctx.author, "id", None),
            source="text",
            persona="derf" if response_key_prefix == DERF_RESPONSE_KEY else "nic",
        ),
    )


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
        await asyncio.to_thread(
            redis_client.lpush, queue_name, f"{unique_id}|{index}|{msg}"
        )
        index += 1


# Wrappers for specific audio queues
async def process_derf_audio_queue(unique_id: str, messages: list[str]):
    await process_audio_queue(unique_id, messages, DERF_AUDIO_QUEUE)


async def process_nic_audio_queue(unique_id: str, messages: list[str]):
    await process_audio_queue(unique_id, messages, NIC_AUDIO_QUEUE)
