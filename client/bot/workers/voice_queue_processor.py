import json
import asyncio
import logging
import os
import uuid
from bot.redis_client import redis_client

from bot.pipelines.unified import RequestContext, run_unified_response_pipeline

from bot.processing import process_derf_audio_queue, process_nic_audio_queue
from bot.config import CHAT_CHANNEL_ID
from bot.constants import (
    DERF_RESPONSE_QUEUE,
    DERF_SUMMARIZER_QUEUE,
    NIC_SUMMARIZER_QUEUE,
    VOICE_NIC_RESPONSE_QUEUE,
    VOICE_RESPONSE_DEAD_QUEUE,
    DERF_AUDIO_QUEUE,
    NIC_AUDIO_QUEUE,
)

logger = logging.getLogger(__name__)

# Repeating what was asked back to the chat channel is a debug aid; off by
# default so normal replies appear only once.
ECHO_PROMPT_TO_CHAT = os.getenv("ECHO_PROMPT_TO_CHAT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Requeue failed items this many times before dead-lettering them.
MAX_PIPELINE_ATTEMPTS = 3


async def process_response_queue(
    queue_name, bot_instance, process_audio_func, summarizer_queue_name
):
    """Generic function to process a Redis response queue.

    Items that fail mid-pipeline are requeued with an attempt counter; after
    MAX_PIPELINE_ATTEMPTS they go to the dead-letter queue instead of vanishing
    (a BLPOP'd item used to be lost on any error).
    """
    logger.info(f"Monitoring {queue_name}...")

    async def requeue_or_deadletter(item: dict, attempt: int, reason: str) -> None:
        if attempt < MAX_PIPELINE_ATTEMPTS:
            item["attempt"] = attempt + 1
            await asyncio.to_thread(redis_client.lpush, queue_name, json.dumps(item))
            logger.warning(
                "voice_pipeline.requeue queue=%s attempt=%s reason=%s",
                queue_name,
                attempt + 1,
                reason,
            )
        else:
            item["dead_reason"] = reason
            await asyncio.to_thread(
                redis_client.lpush, VOICE_RESPONSE_DEAD_QUEUE, json.dumps(item)
            )
            logger.error(
                "voice_pipeline.dead_letter queue=%s attempt=%s reason=%s",
                queue_name,
                attempt,
                reason,
            )

    while True:
        try:
            # Block for work (in a thread) so we don't poll/sleep. Producers
            # LPUSH, so consuming from the tail (BRPOP) keeps burst messages in
            # the order they were spoken; BLPOP would answer them backwards.
            result = await asyncio.to_thread(redis_client.brpop, queue_name, 30)
            if not result or len(result) < 2:
                continue
            _, queued_item = result

            logger.info(f"Received queued item from {queue_name}: {queued_item}")

            try:
                data = json.loads(queued_item)
            except json.JSONDecodeError as e:
                logger.warning(
                    "voice_pipeline.bad_json queue=%s error=%s", queue_name, e
                )
                continue

            attempt = int(data.get("attempt", 1) or 1)
            unique_id = data["unique_id"]
            message = data["message"]
            trace_id = data.get("trace_id") or str(uuid.uuid4())
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            user_id = data.get("user_id")

            # Define a fallback channel ID for automated responses
            fallback_channel_id = CHAT_CHANNEL_ID

            # Fetch the channel
            channel = bot_instance.get_channel(fallback_channel_id)
            if not channel:
                logger.info(f"Channel {fallback_channel_id} not found.")
                await requeue_or_deadletter(data, attempt, "channel_not_found")
                continue

            # Check for voice channel users
            voice_client = channel.guild.voice_client
            human_in_voice_channel = (
                voice_client is not None
                and voice_client.channel is not None
                and any(not m.bot for m in voice_client.channel.members)
            )
            logger.info(f"Human in voice channel: {human_in_voice_channel}")

            # Unified pipeline: LLM + optional summarize + optional TTS enqueue.
            # Choose the correct audio queue based on which response queue we consumed.
            audio_queue_name = (
                NIC_AUDIO_QUEUE
                if queue_name == VOICE_NIC_RESPONSE_QUEUE
                else DERF_AUDIO_QUEUE
            )

            logger.info(
                "voice_pipeline.dispatch trace_id=%s unique_id=%s persona=%s audio_queue=%s",
                trace_id,
                unique_id,
                "nic" if queue_name == VOICE_NIC_RESPONSE_QUEUE else "derf",
                audio_queue_name,
            )

            try:
                await run_unified_response_pipeline(
                    bot_instance=bot_instance,
                    send=channel.send,
                    prompt_text=message,
                    unique_id=str(unique_id),
                    summarizer_queue_name=summarizer_queue_name,
                    audio_queue_name=audio_queue_name,
                    human_in_voice_channel=human_in_voice_channel,
                    ctx=RequestContext(
                        trace_id=str(trace_id),
                        guild_id=guild_id,
                        channel_id=channel_id,
                        user_id=user_id,
                        source="voice",
                        persona=(
                            "nic" if queue_name == VOICE_NIC_RESPONSE_QUEUE else "derf"
                        ),
                    ),
                    echo_prompt_to_chat=ECHO_PROMPT_TO_CHAT,
                )
            except Exception as e:
                # LLMRequestError and other pipeline failures requeue the item
                # (with a cap) instead of dropping it.
                logger.exception(
                    "voice_pipeline.failed queue=%s error=%s", queue_name, e
                )
                await requeue_or_deadletter(data, attempt, f"pipeline_error: {e}")

        except Exception as e:
            logger.exception(f"Error in processing {queue_name}: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors


async def monitor_nic_response_queue(bot):
    """Monitor the Redis voice response queue for Nic."""
    await process_response_queue(
        VOICE_NIC_RESPONSE_QUEUE,
        bot,
        process_nic_audio_queue,
        NIC_SUMMARIZER_QUEUE,
    )


async def monitor_derf_response_queue(bot):
    """Monitor the Redis voice response queue."""
    await process_response_queue(
        DERF_RESPONSE_QUEUE, bot, process_derf_audio_queue, DERF_SUMMARIZER_QUEUE
    )
