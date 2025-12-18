import json
import asyncio
import logging
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
    DERF_AUDIO_QUEUE,
    NIC_AUDIO_QUEUE,
)

logger = logging.getLogger(__name__)


async def process_response_queue(
    queue_name, bot_instance, process_audio_func, summarizer_queue_name
):
    """Generic function to process a Redis response queue."""
    logger.info(f"Monitoring {queue_name}...")
    while True:
        try:
            # Block for work (in a thread) so we don't poll/sleep.
            result = await asyncio.to_thread(redis_client.blpop, queue_name, 30)
            if not result or len(result) < 2:
                continue
            _, queued_item = result

            logger.info(f"Received queued item from {queue_name}: {queued_item}")

            # Parse the queued item
            data = json.loads(queued_item)
            unique_id = data["unique_id"]
            message = data["message"]
            trace_id = data.get("trace_id") or str(uuid.uuid4())
            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            user_id = data.get("user_id")

            # Extract the channel ID and user ID from the message
            # user_id, actual_message = message.split(":", 1)
            # user_id = int(user_id)

            # Define a fallback channel ID for automated responses
            fallback_channel_id = CHAT_CHANNEL_ID

            # Fetch the channel
            channel = bot_instance.get_channel(fallback_channel_id)
            if not channel:
                logger.info(f"Channel {fallback_channel_id} not found.")
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
            audio_queue_name = NIC_AUDIO_QUEUE if queue_name == VOICE_NIC_RESPONSE_QUEUE else DERF_AUDIO_QUEUE

            logger.info(
                "voice_pipeline.dispatch trace_id=%s unique_id=%s persona=%s audio_queue=%s",
                trace_id,
                unique_id,
                "nic" if queue_name == VOICE_NIC_RESPONSE_QUEUE else "derf",
                audio_queue_name,
            )

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
                    persona="nic" if queue_name == VOICE_NIC_RESPONSE_QUEUE else "derf",
                ),
                echo_prompt_to_chat=True,
            )

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
