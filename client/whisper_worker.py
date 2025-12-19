import os
import re
import json
import time
import wave
from random import randint
import logging
import asyncio
import aiohttp
from bot.db import SQLiteDB
from bot.redis_client import redis_client
from bot.constants import (
    WHISPER_QUEUE,
    WHISPER_INFLIGHT_QUEUE,
    WHISPER_DEAD_QUEUE,
    VOICE_RESPONSE_QUEUE,
    VOICE_NIC_RESPONSE_QUEUE,
)

logger = logging.getLogger(__name__)


def _ensure_logging_configured() -> None:
    """Ensure INFO logs are visible when whisper_worker is run standalone.

    If the parent process already configured logging, do not override it.
    """

    root = logging.getLogger()
    if root.handlers:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


bot_name_pattern = re.compile(r"\b(bot|derf|derfbot|dorf|dwarf)\b", re.IGNORECASE)
nic_bot_name_pattern = re.compile(r"\b(nic|nick|nicole|nikky|nik)\b", re.IGNORECASE)

# Initialize the database
db = SQLiteDB()
db.create_table()


class WhisperClient:
    def __init__(self, session: aiohttp.ClientSession, *, url: str):
        self._session = session
        self._url = url

    async def get_text(self, audio_file_path: str, *, trace_id: str | None = None) -> str:
        url = self._url
        headers = {
            "accept": "application/json",
        }
        started = time.perf_counter()
        try:
            form = aiohttp.FormData()
            with open(audio_file_path, "rb") as f:
                form.add_field(
                    "file",
                    f,
                    filename=os.path.basename(audio_file_path),
                    content_type="audio/wav",
                )
                async with self._session.post(url, headers=headers, data=form) as response:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    if response.status == 200:
                        json_response = await response.json()
                        text = json_response.get("text", "")
                        logger.info(
                            "whisper.http_ok trace_id=%s status=%s elapsed_ms=%s text_len=%s",
                            trace_id,
                            response.status,
                            elapsed_ms,
                            len(text or ""),
                        )
                        return text

                    body = await response.text()
                    logger.warning(
                        "whisper.http_error trace_id=%s status=%s elapsed_ms=%s body=%s",
                        trace_id,
                        response.status,
                        elapsed_ms,
                        (body[:500] + "…") if len(body) > 500 else body,
                    )
                    return ""
        except asyncio.TimeoutError:
            logger.warning("whisper.timeout trace_id=%s", trace_id)
            return ""
        except Exception as e:
            logger.exception("whisper.exception trace_id=%s error=%s", trace_id, e)
            import traceback

            traceback.print_exc()
            return ""


def _wav_info(path: str) -> dict:
    """Best-effort WAV metadata helper (runs in a thread)."""
    info: dict = {}
    try:
        with wave.open(path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            info["channels"] = wf.getnchannels()
            info["sample_width"] = wf.getsampwidth()
            info["frame_rate"] = rate
            info["frames"] = frames
            if rate:
                info["duration_ms"] = int((frames / rate) * 1000)
    except Exception:
        return info
    return info


class WhisperWorker:
    def __init__(self):
        # Tunables (env override friendly)
        self._whisper_url = os.getenv("WHISPER_URL", "http://127.0.0.1:8080/inference")
        self._max_attempts = int(os.getenv("WHISPER_MAX_ATTEMPTS", "5"))
        self._retry_backoff_s = float(os.getenv("WHISPER_RETRY_BACKOFF_S", "1.0"))

    async def process_audio(self):
        """Process audio paths from the Redis queue."""
        _ensure_logging_configured()
        # Connect to Redis
        logger.info("Connecting to Redis")
        if not redis_client.ping():
            raise ConnectionError("Failed to connect to Redis.")
        try:
            qlen = redis_client.llen(WHISPER_QUEUE)
        except Exception:
            qlen = "?"
        logger.info(
            "Connected to Redis successfully. whisper_url=%s queue=%s inflight=%s dead=%s initial_len=%s",
            self._whisper_url,
            WHISPER_QUEUE,
            WHISPER_INFLIGHT_QUEUE,
            WHISPER_DEAD_QUEUE,
            qlen,
        )

        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            whisper_client = WhisperClient(session, url=self._whisper_url)
            last_job_time = 0.0
            last_idle_log = 0.0
            while True:
                try:
                    # Use an inflight list so jobs are not silently lost if the worker
                    # crashes or Whisper errors out.
                    raw_value = await asyncio.to_thread(
                        redis_client.brpoplpush,
                        WHISPER_QUEUE,
                        WHISPER_INFLIGHT_QUEUE,
                        30,
                    )

                    if not raw_value:
                        now = time.time()
                        # Heartbeat: show queue length periodically when idle.
                        if now - last_idle_log >= 30.0:
                            try:
                                qlen = await asyncio.to_thread(redis_client.llen, WHISPER_QUEUE)
                                inflight_len = await asyncio.to_thread(redis_client.llen, WHISPER_INFLIGHT_QUEUE)
                            except Exception:
                                qlen = "?"
                                inflight_len = "?"
                            idle_for = int(now - last_job_time) if last_job_time else None
                            logger.info(
                                "whisper.idle queue=%s len=%s inflight_len=%s idle_for_s=%s",
                                WHISPER_QUEUE,
                                qlen,
                                inflight_len,
                                idle_for,
                            )
                            last_idle_log = now
                        continue

                    # Value contains JSON metadata.
                    logger.info(
                        "whisper.dequeue queue=%s inflight=%s bytes=%s",
                        WHISPER_QUEUE,
                        WHISPER_INFLIGHT_QUEUE,
                        len(raw_value) if raw_value else 0,
                    )

                    last_job_time = time.time()

                    try:
                        path_info = json.loads(raw_value)
                    except json.JSONDecodeError as e:
                        logger.warning("whisper.bad_json error=%s raw=%s", e, (raw_value[:500] + "…") if len(raw_value) > 500 else raw_value)
                        # Drop poison pill from inflight.
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                        continue

                    user_id = path_info.get("user_id")
                    audio_path = path_info.get("audio_path")
                    trace_id = path_info.get("trace_id")
                    guild_id = path_info.get("guild_id")
                    channel_id = path_info.get("channel_id")
                    attempt = int(path_info.get("attempt", 1) or 1)

                    if not user_id or not audio_path:
                        logger.info("No valid user_id or audio_path in the received data.")
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                        continue

                    exists = os.path.exists(audio_path)
                    size_bytes = os.path.getsize(audio_path) if exists else 0
                    wav_meta = await asyncio.to_thread(_wav_info, audio_path) if exists else {}

                    logger.info(
                        "whisper.job trace_id=%s user_id=%s guild_id=%s channel_id=%s path=%s exists=%s size_bytes=%s wav=%s",
                        trace_id,
                        user_id,
                        guild_id,
                        channel_id,
                        audio_path,
                        exists,
                        size_bytes,
                        wav_meta,
                    )

                    if not exists:
                        logger.warning("whisper.missing_audio trace_id=%s path=%s", trace_id, audio_path)
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                        continue

                    text_response = await whisper_client.get_text(audio_path, trace_id=trace_id)
                    if not text_response:
                        logger.info(
                            "whisper.empty_response trace_id=%s attempt=%s max_attempts=%s",
                            trace_id,
                            attempt,
                            self._max_attempts,
                        )
                        # Retry a few times; Whisper being down or busy should not permanently drop audio.
                        if attempt < self._max_attempts:
                            path_info["attempt"] = attempt + 1
                            path_info["last_error"] = "empty_response"
                            await asyncio.to_thread(redis_client.lpush, WHISPER_QUEUE, json.dumps(path_info))
                            await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                            await asyncio.sleep(self._retry_backoff_s)
                            continue

                        # Dead-letter after max retries; keep audio on disk for postmortem.
                        path_info["attempt"] = attempt
                        path_info["dead_reason"] = "empty_response"
                        await asyncio.to_thread(redis_client.lpush, WHISPER_DEAD_QUEUE, json.dumps(path_info))
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                        continue

                    text_response = text_response.strip()
                    preview = (text_response[:200] + "…") if len(text_response) > 200 else text_response
                    logger.info(
                        "whisper.transcript trace_id=%s text_len=%s preview=%s",
                        trace_id,
                        len(text_response),
                        preview,
                    )
                    db.insert_entry(user_id, text_response)

                    payload = {
                        "unique_id": str(randint(100000, 999999)),
                        "message": text_response,
                    }
                    # Carry metadata forward for multi-guild routing later.
                    if trace_id:
                        payload["trace_id"] = trace_id
                    if guild_id:
                        payload["guild_id"] = guild_id
                    if channel_id:
                        payload["channel_id"] = channel_id
                    payload["user_id"] = user_id

                    if bot_name_pattern.search(text_response):
                        redis_client.lpush(VOICE_RESPONSE_QUEUE, json.dumps(payload))
                        logger.info("whisper.routed trace_id=%s queue=%s", trace_id, VOICE_RESPONSE_QUEUE)
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                    elif nic_bot_name_pattern.search(text_response):
                        redis_client.lpush(VOICE_NIC_RESPONSE_QUEUE, json.dumps(payload))
                        logger.info("whisper.routed trace_id=%s queue=%s", trace_id, VOICE_NIC_RESPONSE_QUEUE)
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)
                    else:
                        logger.info(
                            "whisper.unrouted trace_id=%s reason=no_bot_name preview=%s",
                            trace_id,
                            preview,
                        )
                        # Keep audio file if we didn't route it.
                        await asyncio.to_thread(redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value)

                except Exception as e:
                    # Important: if we crashed mid-processing, keep the job in inflight.
                    # The watchdog/ops can decide to requeue inflight later.
                    logger.exception("whisper.loop_exception error=%s", e)
                    await asyncio.sleep(0.25)


def main():
    worker = WhisperWorker()
    asyncio.run(worker.process_audio())


if __name__ == "__main__":
    main()
