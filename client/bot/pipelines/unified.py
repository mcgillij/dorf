import asyncio
import logging
import time
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from bot.constants import LONG_RESPONSE_THRESHOLD, SUMMARIZER_RESPONSE_KEY
from bot.redis_client import redis_client
from bot.utilities import split_message, poll_redis_for_key_with_timeout

logger = logging.getLogger(__name__)


SUMMARIZER_MAX_WAIT_S = 15.0


SendCallable = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class RequestContext:
    trace_id: str
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    user_id: Optional[int] = None
    source: str = "unknown"  # voice|text|other
    persona: Optional[str] = None  # derf|nic|...


async def _redis_lpush(key: str, value: str) -> None:
    result = await asyncio.to_thread(redis_client.lpush, key, value)
    logger.debug("redis.lpush key=%s new_len=%s", key, result)


async def _redis_set(key: str, value: str) -> None:
    await asyncio.to_thread(redis_client.set, key, value)


async def send_text(send: SendCallable, text: str, *, max_len: int = 2000) -> None:
    for chunk in split_message(text or "", max_len):
        await send(chunk)


async def summarize_if_needed(
    *,
    response_text: str,
    unique_id: str,
    summarizer_queue: str,
    ctx: RequestContext,
) -> str:
    if not response_text:
        return ""

    if len(response_text) <= LONG_RESPONSE_THRESHOLD:
        return response_text

    logger.info(
        "summarize.enqueue trace_id=%s unique_id=%s len=%s",
        ctx.trace_id,
        unique_id,
        len(response_text),
    )
    await _redis_lpush(
        summarizer_queue,
        json.dumps({"unique_id": unique_id, "message": response_text}),
    )

    summary_key = f"{SUMMARIZER_RESPONSE_KEY}:{unique_id}"
    summary = await poll_redis_for_key_with_timeout(
        summary_key,
        max_wait_s=SUMMARIZER_MAX_WAIT_S,
        poll_interval_s=0.5,
        delete=True,
    )

    if summary is None:
        logger.error(
            "summarize.timeout trace_id=%s unique_id=%s waited_s=%s summary_key=%s",
            ctx.trace_id,
            unique_id,
            SUMMARIZER_MAX_WAIT_S,
            summary_key,
        )
        return response_text

    summary = (summary or "").strip()
    if not summary:
        logger.error(
            "summarize.empty trace_id=%s unique_id=%s summary_key=%s",
            ctx.trace_id,
            unique_id,
            summary_key,
        )
        return response_text

    return summary


async def enqueue_tts(
    *,
    audio_queue_name: str,
    unique_id: str,
    messages: list[str],
    ctx: RequestContext,
) -> None:
    index = 1
    for msg in messages:
        payload = f"{unique_id}|{index}|{msg}"
        logger.info(
            "tts.enqueue trace_id=%s unique_id=%s queue=%s index=%s text_len=%s payload_bytes=%s",
            ctx.trace_id,
            unique_id,
            audio_queue_name,
            index,
            len(msg or ""),
            len(payload),
        )
        await _redis_lpush(audio_queue_name, payload)
        index += 1


async def run_unified_response_pipeline(
    *,
    bot_instance,
    send: SendCallable,
    prompt_text: str,
    unique_id: str,
    summarizer_queue_name: str,
    audio_queue_name: str,
    human_in_voice_channel: bool,
    ctx: RequestContext,
    echo_prompt_to_chat: bool = True,
) -> str:
    """Unified pipeline:

    - (optional) echo prompt to chat
    - get LLM response
    - send response
    - optionally summarize and send
    - optionally enqueue TTS

    All Redis operations are pushed off the event loop via asyncio.to_thread.
    """

    started = time.time()
    logger.info(
        "pipeline.start source=%s persona=%s trace_id=%s unique_id=%s guild_id=%s channel_id=%s user_id=%s",
        ctx.source,
        ctx.persona,
        ctx.trace_id,
        unique_id,
        ctx.guild_id,
        ctx.channel_id,
        ctx.user_id,
    )

    if echo_prompt_to_chat:
        await send_text(send, prompt_text)

    response_text = await bot_instance.llm.get_response(prompt_text)
    response_text = response_text or ""

    await send_text(send, response_text)

    final_for_voice = await summarize_if_needed(
        response_text=response_text,
        unique_id=unique_id,
        summarizer_queue=summarizer_queue_name,
        ctx=ctx,
    )

    if final_for_voice != response_text:
        await send_text(send, final_for_voice)

    if human_in_voice_channel:
        await enqueue_tts(
            audio_queue_name=audio_queue_name,
            unique_id=unique_id,
            messages=[final_for_voice if final_for_voice else response_text],
            ctx=ctx,
        )

    logger.info(
        "pipeline.done trace_id=%s unique_id=%s response_len=%s elapsed_ms=%s",
        ctx.trace_id,
        unique_id,
        len(response_text),
        int((time.time() - started) * 1000),
    )

    return response_text


async def deliver_existing_response(
    *,
    send: SendCallable,
    response_text: str,
    unique_id: str,
    summarizer_queue_name: str,
    audio_queue_name: str,
    human_in_voice_channel: bool,
    ctx: RequestContext,
) -> None:
    """Deliver a response that was computed elsewhere (e.g., via response worker).

    Keeps the same summarization + TTS behavior as the unified pipeline.
    """

    response_text = response_text or ""
    await send_text(send, response_text)

    final_for_voice = await summarize_if_needed(
        response_text=response_text,
        unique_id=unique_id,
        summarizer_queue=summarizer_queue_name,
        ctx=ctx,
    )
    if final_for_voice != response_text:
        await send_text(send, final_for_voice)

    if human_in_voice_channel:
        await enqueue_tts(
            audio_queue_name=audio_queue_name,
            unique_id=unique_id,
            messages=[final_for_voice if final_for_voice else response_text],
            ctx=ctx,
        )
