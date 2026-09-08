import asyncio
import json
import logging
import os

from bot.constants import (
    DERF_RESPONSE_KEY,
    DERF_RESPONSE_KEY_PREFIX,
    NIC_RESPONSE_KEY,
    NIC_RESPONSE_KEY_PREFIX,
    TTS_VOICE,
)
from bot.redis_client import redis_client
from bot.tts import synthesize

logger = logging.getLogger(__name__)

# Desktop-pet speech: key maps unique_id → abs wav path (or "failed"), TTL
# bounds both the served window and the pet's polling budget. Mirrored by
# GET /api/tts/{unique_id} in the api process.
PET_TTS_KEY_PREFIX = "pet_tts"
PET_TTS_TTL_S = 600


async def process_response_queue(queue_name, response_key_prefix, bot):
    """
    Continuously process requests for get_response from a specified Redis queue.
    """
    while True:
        try:
            task_data = await asyncio.to_thread(redis_client.rpop, queue_name)
            if not task_data:
                await asyncio.sleep(1)
                continue
            logger.info(f"{queue_name}: Received task data: {task_data}")

            # Parse task data
            task = json.loads(task_data)
            unique_id = task["unique_id"]
            message = task["message"]

            # Call get_response. On LLM failure, still set the key (empty) so
            # the waiting text command gets a prompt "no response" instead of
            # polling until its timeout.
            try:
                response = await bot.llm.get_response(message)
            except Exception as e:
                logger.error(f"{queue_name}: LLM request failed: {e}")
                response = ""

            # Store the response in Redis for retrieval. TTL guards against
            # leaks when the poller timed out or died before deleting the key.
            await asyncio.to_thread(
                redis_client.set,
                f"{response_key_prefix}:{unique_id}",
                response,
                ex=3600,
            )

            # Pet queries never propagate to Discord chat or voice — the
            # response is consumed via /api/fetch_response (command-side
            # audio enqueuing is unreachable from here). Their speech happens
            # locally instead.
            if task.get("source") == "godot":
                await serve_pet_tts(unique_id, response)
        except Exception as e:
            logger.exception(f"{queue_name}: Error processing response queue: {e}")


async def serve_pet_tts(unique_id: str, response: str) -> None:
    """Synthesize local speech for a desktop-pet query after the LLM.

    Reuses the already-loaded provider (get_provider() instance cache). Text
    never blocks on audio: any failure — including an empty response —
    degrades to text-only via the "failed" marker, which the api turns into a
    404 so the pet gives up fast instead of polling its full budget.
    """
    key = f"{PET_TTS_KEY_PREFIX}:{unique_id}"
    try:
        if not response.strip():
            raise ValueError("empty response — nothing to synthesize")
        output_dir = os.path.abspath(os.getenv("AUDIO_OUTPUT_DIR", "output"))
        os.makedirs(output_dir, exist_ok=True)
        wav_path = os.path.join(output_dir, f"pet_{unique_id}.wav")
        await asyncio.to_thread(
            synthesize, response, TTS_VOICE, wav_path
        )
        await asyncio.to_thread(redis_client.set, key, wav_path, ex=PET_TTS_TTL_S)
        logger.info(
            "response_worker.pet_tts_ready unique_id=%s wav=%s", unique_id, wav_path
        )
    except Exception as e:
        logger.exception(
            "response_worker.pet_tts_failed unique_id=%s err=%s", unique_id, e
        )
        await asyncio.to_thread(redis_client.set, key, "failed", ex=PET_TTS_TTL_S)


async def process_derf_response_queue(bot):
    await process_response_queue(DERF_RESPONSE_KEY_PREFIX, DERF_RESPONSE_KEY, bot)


async def process_nic_response_queue(bot):
    await process_response_queue(NIC_RESPONSE_KEY_PREFIX, NIC_RESPONSE_KEY, bot)
