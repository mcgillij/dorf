import os
import re
import json
import time
import wave
import uuid
import logging
import signal
import asyncio
import fnmatch
import aiohttp
from bot.db import SQLiteDB, ensure_sqlite_pragmas, sweep_voice_responses
from bot.redis_client import redis_client
from bot.constants import (
    WHISPER_QUEUE,
    WHISPER_INFLIGHT_QUEUE,
    WHISPER_DEAD_QUEUE,
    VOICE_RESPONSE_QUEUE,
    VOICE_NIC_RESPONSE_QUEUE,
    VOICE_CONTROL_DERF_QUEUE,
    VOICE_CONTROL_NIC_QUEUE,
)

logger = logging.getLogger(__name__)

# Atomically move a job from the inflight list back onto the main queue,
# replacing it with an updated payload (bumped attempt counter). Doing
# LPUSH+LREM separately left a duplicated job in both lists if the worker
# died between the two calls.
_RETRY_MOVE_LUA = """
if redis.call('LREM', KEYS[1], 1, ARGV[1]) == 1 then
  return redis.call('LPUSH', KEYS[2], ARGV[2])
end
return 0
"""

# Singleton lock: only one worker may consume whisper_queue. A second
# worker's reaper would steal in-flight jobs from the live one and produce
# duplicate transcripts/responses (double speech).
_WORKER_LOCK_KEY = "whisper_worker_lock"
# Generous TTL: the idle loop refreshes every ~10s (brpoplpush timeout),
# so 90s survives several missed cycles. The original 30s TTL raced the
# 30s brpoplpush block — on an empty queue the first refresh ran at the
# exact moment of expiry and always lost (lock lost -> worker exit).
_WORKER_LOCK_TTL_S = 90

# Refresh the lock TTL only if we still own it (never steal someone else's).
_LOCK_REFRESH_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def _remove_audio(audio_path: str | None) -> None:
    """Best-effort deletion of a transcribed wav (never raises)."""
    if not audio_path:
        return
    try:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    except OSError:
        logger.warning(
            "whisper.audio_cleanup_failed path=%s", audio_path, exc_info=True
        )


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
stop_pattern = re.compile(r"\b(stop|shut\s*-?\s*up|shutup)\b", re.IGNORECASE)

# Initialize the database
db = SQLiteDB()
db.create_table()


class WhisperClient:
    def __init__(self, session: aiohttp.ClientSession, *, url: str):
        self._session = session
        self._url = url

    async def get_text(
        self, audio_file_path: str, *, trace_id: str | None = None
    ) -> str:
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
                async with self._session.post(
                    url, headers=headers, data=form
                ) as response:
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
        # How long a follow-up stays with the bot that answered the last
        # wake-word turn, without needing the wake word again.
        self._session_ttl_s = int(os.getenv("VOICE_SESSION_TTL_S", "60"))
        # A job stuck in inflight longer than this is considered stranded
        # (requeued by the idle reaper). Must exceed the whisper HTTP timeout.
        self._lease_s = int(os.getenv("WHISPER_LEASE_S", "120"))
        # Retention for captured utterance wavs: only the success paths in
        # process_audio delete a job's wav, so everything older than this is
        # swept hourly (see sweep_old_audio) to bound disk usage.
        self._audio_root = os.getenv("USER_AUDIO_DIR", "user_audio")
        self._retention_hours = float(
            os.getenv("USER_AUDIO_RETENTION_HOURS", "24")
        )
        self._lock_token = f"{os.getpid()}-{int(time.time())}"

    async def _acquire_singleton_lock(self) -> None:
        acquired = await asyncio.to_thread(
            redis_client.set,
            _WORKER_LOCK_KEY,
            self._lock_token,
            nx=True,
            ex=_WORKER_LOCK_TTL_S,
        )
        if not acquired:
            raise SystemExit(
                "Another whisper worker holds whisper_worker_lock; exiting to "
                "avoid duplicate transcription (see docs/improvement.md P1-10)."
            )
        logger.info("whisper.singleton_lock_acquired token=%s", self._lock_token)

    async def _refresh_singleton_lock(self) -> bool:
        """Refresh the lock TTL if we still own it. False = we lost it."""
        renewed = await asyncio.to_thread(
            redis_client.eval,
            _LOCK_REFRESH_LUA,
            1,
            _WORKER_LOCK_KEY,
            self._lock_token,
            _WORKER_LOCK_TTL_S * 1000,
        )
        return bool(renewed)

    async def _requeue_stranded_inflight(self) -> int:
        """Move jobs stranded in the inflight list back to the main queue.

        Jobs land in inflight via BRPOPLPUSH; if a previous worker crashed
        mid-job they used to stay there forever with no reaper.
        """
        moved = 0
        while True:
            raw = await asyncio.to_thread(
                redis_client.rpoplpush, WHISPER_INFLIGHT_QUEUE, WHISPER_QUEUE
            )
            if not raw:
                break
            moved += 1
        if moved:
            logger.warning("whisper.inflight_requeued moved=%s", moved)
        return moved

    async def sweep_old_audio(self) -> None:
        """Delete captured utterance wavs older than the retention window.

        Unrouted utterances, whisper failures and dead-lettered jobs keep
        their wavs on disk forever (only the success paths unlink them);
        this hourly sweep bounds that growth.
        """
        deleted_files, deleted_bytes, deleted_dirs = await asyncio.to_thread(
            self._sweep_old_audio_sync
        )
        logger.info(
            "whisper.audio_sweep_done files=%s bytes=%s dirs=%s root=%s retention_h=%s",
            deleted_files,
            deleted_bytes,
            deleted_dirs,
            self._audio_root,
            self._retention_hours,
        )

    def _sweep_old_audio_sync(self) -> tuple[int, int, int]:
        """Walk the audio root, unlink stale chunk wavs, prune empty dirs.

        Returns (deleted_files, deleted_bytes, deleted_dirs). Runs in a
        thread; per-file failures are logged and skipped.
        """
        cutoff = time.time() - self._retention_hours * 3600.0
        root_abs = os.path.abspath(self._audio_root)
        deleted_files = 0
        deleted_bytes = 0
        deleted_dirs = 0
        for dirpath, _dirnames, filenames in os.walk(self._audio_root):
            for name in filenames:
                if not (
                    fnmatch.fnmatch(name, "chunk-*.wav")
                    or fnmatch.fnmatch(name, "*-original.wav")
                ):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    logger.warning(
                        "whisper.audio_sweep_stat_failed path=%s",
                        path,
                        exc_info=True,
                    )
                    continue
                if st.st_mtime >= cutoff:
                    continue
                try:
                    os.remove(path)
                except OSError:
                    logger.warning(
                        "whisper.audio_sweep_remove_failed path=%s",
                        path,
                        exc_info=True,
                    )
                    continue
                deleted_files += 1
                deleted_bytes += st.st_size
        # Prune user dirs the sweep emptied (deepest first). rmdir only
        # succeeds on empty dirs, so the root and non-empty dirs survive.
        for dirpath, _dirnames, _filenames in os.walk(
            self._audio_root, topdown=False
        ):
            if os.path.abspath(dirpath) == root_abs:
                continue
            try:
                os.rmdir(dirpath)
            except OSError:
                continue
            deleted_dirs += 1
        return deleted_files, deleted_bytes, deleted_dirs

    async def process_audio(self):
        """Process audio paths from the Redis queue."""
        _ensure_logging_configured()
        # WAL + busy_timeout before anything touches the DBs (idempotent).
        ensure_sqlite_pragmas(("voice_responses.db",))
        # Connect to Redis
        logger.info("Connecting to Redis")
        if not await asyncio.to_thread(redis_client.ping):
            raise ConnectionError("Failed to connect to Redis.")
        await self._acquire_singleton_lock()
        # Refresh immediately: the acquire TTL clock is already running and
        # the first loop iteration may block for the full brpoplpush timeout.
        await self._refresh_singleton_lock()
        await self._requeue_stranded_inflight()
        try:
            qlen = await asyncio.to_thread(redis_client.llen, WHISPER_QUEUE)
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
            last_sweep = 0.0
            last_lock_refresh = 0.0
            last_reap = 0.0
            while True:
                raw_value = None
                try:
                    # Use an inflight list so jobs are not silently lost if the worker
                    # crashes or Whisper errors out. 10s block: the idle branch
                    # (lock refresh, sweep, reap) must run far more often than
                    # the lock TTL — a 30s block raced the 30s TTL and starved
                    # the refresh on every quiet period.
                    raw_value = await asyncio.to_thread(
                        redis_client.brpoplpush,
                        WHISPER_QUEUE,
                        WHISPER_INFLIGHT_QUEUE,
                        10,
                    )

                    if not raw_value:
                        now = time.time()
                        # Keep the singleton lock alive while idle.
                        if now - last_lock_refresh >= 10.0:
                            if not await self._refresh_singleton_lock():
                                logger.error(
                                    "whisper.singleton_lock_lost — another worker "
                                    "took over; exiting."
                                )
                                return
                            last_lock_refresh = now
                        # Idle reaper: we hold no job (we're in the idle branch)
                        # and the singleton lock guarantees no other live worker,
                        # so anything sitting in inflight is stranded. Requeue it.
                        if (
                            last_job_time
                            and now - last_job_time > self._lease_s
                            and now - last_reap > self._lease_s
                        ):
                            try:
                                inflight_len = await asyncio.to_thread(
                                    redis_client.llen, WHISPER_INFLIGHT_QUEUE
                                )
                            except Exception:
                                inflight_len = 0
                            if inflight_len:
                                last_reap = now
                                moved = 0
                                while True:
                                    raw = await asyncio.to_thread(
                                        redis_client.rpoplpush,
                                        WHISPER_INFLIGHT_QUEUE,
                                        WHISPER_QUEUE,
                                    )
                                    if not raw:
                                        break
                                    moved += 1
                                if moved:
                                    logger.warning(
                                        "whisper.inflight_reaped_idle moved=%s",
                                        moved,
                                    )
                        # Heartbeat: show queue length periodically when idle.
                        if now - last_idle_log >= 30.0:
                            try:
                                qlen = await asyncio.to_thread(
                                    redis_client.llen, WHISPER_QUEUE
                                )
                                inflight_len = await asyncio.to_thread(
                                    redis_client.llen, WHISPER_INFLIGHT_QUEUE
                                )
                            except Exception:
                                qlen = "?"
                                inflight_len = "?"
                            idle_for = (
                                int(now - last_job_time) if last_job_time else None
                            )
                            logger.info(
                                "whisper.idle queue=%s len=%s inflight_len=%s idle_for_s=%s",
                                WHISPER_QUEUE,
                                qlen,
                                inflight_len,
                                idle_for,
                            )
                            last_idle_log = now
                        # Periodic retention sweep: wavs from unrouted,
                        # failed and dead-lettered jobs are never unlinked
                        # on the job paths, so remove stale ones here. Also
                        # prunes transcript history (voice_responses.db) and
                        # the chroma article store. A sweep failure must
                        # never kill the worker loop.
                        if now - last_sweep >= 3600.0:
                            last_sweep = now
                            try:
                                await self.sweep_old_audio()
                            except Exception:
                                logger.exception("whisper.audio_sweep_failed")
                            try:
                                pruned = await asyncio.to_thread(
                                    sweep_voice_responses, "voice_responses.db", 30
                                )
                                if pruned:
                                    logger.info(
                                        "whisper.voice_responses_sweep pruned=%s", pruned
                                    )
                            except Exception:
                                logger.exception("whisper.voice_responses_sweep_failed")
                            try:
                                # Lazy import: chromadb is heavy and the STT
                                # worker otherwise never needs it.
                                from bot.chroma import sweep_old_documents

                                await sweep_old_documents(max_age_days=90)
                            except Exception:
                                logger.exception("whisper.chroma_sweep_failed")
                        continue

                    # Value contains JSON metadata.
                    logger.info(
                        "whisper.dequeue queue=%s inflight=%s bytes=%s",
                        WHISPER_QUEUE,
                        WHISPER_INFLIGHT_QUEUE,
                        len(raw_value) if raw_value else 0,
                    )

                    last_job_time = time.time()
                    # Refresh the singleton lock before processing: a long
                    # whisper call must not outlive the 30s lock TTL.
                    if not await self._refresh_singleton_lock():
                        logger.error(
                            "whisper.singleton_lock_lost — another worker took "
                            "over; exiting."
                        )
                        return

                    try:
                        path_info = json.loads(raw_value)
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "whisper.bad_json error=%s raw=%s",
                            e,
                            (
                                (raw_value[:500] + "…")
                                if len(raw_value) > 500
                                else raw_value
                            ),
                        )
                        # Drop poison pill from inflight.
                        await asyncio.to_thread(
                            redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                        )
                        continue

                    user_id = path_info.get("user_id")
                    audio_path = path_info.get("audio_path")
                    trace_id = path_info.get("trace_id")
                    guild_id = path_info.get("guild_id")
                    channel_id = path_info.get("channel_id")
                    attempt = int(path_info.get("attempt", 1) or 1)

                    if not user_id or not audio_path:
                        logger.info(
                            "No valid user_id or audio_path in the received data."
                        )
                        await asyncio.to_thread(
                            redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                        )
                        continue

                    exists = os.path.exists(audio_path)
                    size_bytes = os.path.getsize(audio_path) if exists else 0
                    wav_meta = (
                        await asyncio.to_thread(_wav_info, audio_path) if exists else {}
                    )

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
                        logger.warning(
                            "whisper.missing_audio trace_id=%s path=%s",
                            trace_id,
                            audio_path,
                        )
                        await asyncio.to_thread(
                            redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                        )
                        continue

                    text_response = await whisper_client.get_text(
                        audio_path, trace_id=trace_id
                    )
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
                            # Atomically move inflight → queue so a crash
                            # between the two steps can't duplicate the job.
                            # ARGV[1] is the original payload to remove,
                            # ARGV[2] the updated one to push.
                            await asyncio.to_thread(
                                redis_client.eval,
                                _RETRY_MOVE_LUA,
                                2,
                                WHISPER_INFLIGHT_QUEUE,
                                WHISPER_QUEUE,
                                raw_value,
                                json.dumps(path_info),
                            )
                            await asyncio.sleep(self._retry_backoff_s)
                            continue

                        # Dead-letter after max retries; keep audio on disk for postmortem.
                        path_info["attempt"] = attempt
                        path_info["dead_reason"] = "empty_response"
                        await asyncio.to_thread(
                            redis_client.lpush,
                            WHISPER_DEAD_QUEUE,
                            json.dumps(path_info),
                        )
                        await asyncio.to_thread(
                            redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                        )
                        continue

                    text_response = text_response.strip()
                    preview = (
                        (text_response[:200] + "…")
                        if len(text_response) > 200
                        else text_response
                    )
                    logger.info(
                        "whisper.transcript trace_id=%s text_len=%s preview=%s",
                        trace_id,
                        len(text_response),
                        preview,
                    )

                    # Route by wake word first. A transcript must be addressed to a
                    # bot before anything else (including stop commands) happens.
                    if bot_name_pattern.search(text_response):
                        routed = "derf"
                    elif nic_bot_name_pattern.search(text_response):
                        routed = "nic"
                    else:
                        # Session continuation: follow-ups addressed to nobody keep
                        # going to the bot that answered the previous wake-word turn.
                        routed = await asyncio.to_thread(
                            redis_client.get, f"voice_session:{user_id}"
                        )
                        if routed not in ("derf", "nic"):
                            logger.info(
                                "whisper.unrouted trace_id=%s reason=no_bot_name preview=%s",
                                trace_id,
                                preview,
                            )
                            # Keep audio file if we didn't route it.
                            await asyncio.to_thread(
                                redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                            )
                            continue
                        logger.info(
                            "whisper.session_continue trace_id=%s user_id=%s bot=%s",
                            trace_id,
                            user_id,
                            routed,
                        )

                    # Start/refresh the conversation session either way, so the
                    # next nameless follow-up keeps talking to the same bot.
                    await asyncio.to_thread(
                        redis_client.set,
                        f"voice_session:{user_id}",
                        routed,
                        ex=self._session_ttl_s,
                    )

                    # Voice control: stop/shut-up only counts when addressed to a bot,
                    # so ordinary speech containing "stop" can't kill the pipeline.
                    # The stop phrase must be (nearly) the whole utterance — a
                    # substring match hijacked legitimate questions like
                    # "when does the bus stop arriving?".
                    _stop_is_question = (
                        len(text_response) > 25
                        and not stop_pattern.fullmatch(text_response.strip())
                    )
                    if stop_pattern.search(text_response) and not _stop_is_question:
                        # Duplicate suppression: a requeued copy of this job
                        # must not fire a second stop.
                        if trace_id:
                            claimed = await asyncio.to_thread(
                                redis_client.set,
                                f"voice_done:{trace_id}",
                                "1",
                                nx=True,
                                ex=3600,
                            )
                            if not claimed:
                                logger.warning(
                                    "whisper.duplicate_suppressed trace_id=%s (stop path)",
                                    trace_id,
                                )
                                await asyncio.to_thread(
                                    redis_client.lrem,
                                    WHISPER_INFLIGHT_QUEUE,
                                    1,
                                    raw_value,
                                )
                                _remove_audio(audio_path)
                                continue
                        control_payload = {
                            "action": "stop",
                            "target": routed,
                            "trace_id": trace_id,
                            "guild_id": guild_id,
                            "channel_id": channel_id,
                            "user_id": user_id,
                            "message": text_response,
                        }
                        control_queue = (
                            VOICE_CONTROL_DERF_QUEUE
                            if routed == "derf"
                            else VOICE_CONTROL_NIC_QUEUE
                        )
                        await asyncio.to_thread(
                            redis_client.lpush,
                            control_queue,
                            json.dumps(control_payload),
                        )
                        logger.info(
                            "whisper.control_enqueued trace_id=%s action=stop target=%s queue=%s",
                            trace_id,
                            routed,
                            control_queue,
                        )
                        # Clean up audio + inflight; no DB insert or LLM routing.
                        _remove_audio(audio_path)
                        await asyncio.to_thread(
                            redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                        )
                        continue

                    # --- Idempotency claim (normal path) ---
                    # Requeues (crash recovery, idle reaper) must never produce
                    # a second transcript/response for the same trace_id — this
                    # was a concrete double-speech root cause.
                    if trace_id:
                        claimed = await asyncio.to_thread(
                            redis_client.set,
                            f"voice_done:{trace_id}",
                            "1",
                            nx=True,
                            ex=3600,
                        )
                        if not claimed:
                            logger.warning(
                                "whisper.duplicate_suppressed trace_id=%s", trace_id
                            )
                            await asyncio.to_thread(
                                redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                            )
                            _remove_audio(audio_path)
                            continue

                    # --- Ack BEFORE side effects ---
                    # Once acked, a crash can lose the job (rare, at-most-once)
                    # but must not duplicate it on the next startup requeue.
                    await asyncio.to_thread(
                        redis_client.lrem, WHISPER_INFLIGHT_QUEUE, 1, raw_value
                    )
                    try:
                        await asyncio.to_thread(db.insert_entry, user_id, text_response)

                        payload = {
                            # uuid4: randint IDs collide (1-in-900k), and a collision
                            # cross-wires TTS temp paths and summarizer keys.
                            "unique_id": uuid.uuid4().hex,
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

                        response_queue = (
                            VOICE_RESPONSE_QUEUE
                            if routed == "derf"
                            else VOICE_NIC_RESPONSE_QUEUE
                        )
                        await asyncio.to_thread(
                            redis_client.lpush, response_queue, json.dumps(payload)
                        )
                        logger.info(
                            "whisper.routed trace_id=%s queue=%s",
                            trace_id,
                            response_queue,
                        )
                    except Exception:
                        # Side effects may be partially done; release the claim
                        # and requeue (job already acked, so plain LPUSH) so the
                        # next attempt retries cleanly. The fresh claim next
                        # attempt is what keeps this at-least-once without dupes.
                        logger.exception(
                            "whisper.dispatch_failed trace_id=%s", trace_id
                        )
                        if trace_id:
                            await asyncio.to_thread(
                                redis_client.delete, f"voice_done:{trace_id}"
                            )
                        if attempt < self._max_attempts:
                            path_info["attempt"] = attempt + 1
                            path_info["last_error"] = "dispatch_failed"
                            await asyncio.to_thread(
                                redis_client.lpush, WHISPER_QUEUE, json.dumps(path_info)
                            )
                        else:
                            path_info["attempt"] = attempt
                            path_info["dead_reason"] = "dispatch_failed"
                            await asyncio.to_thread(
                                redis_client.lpush,
                                WHISPER_DEAD_QUEUE,
                                json.dumps(path_info),
                            )
                        continue
                    _remove_audio(audio_path)

                except Exception as e:
                    # Requeue-or-dead-letter the job we were holding so it
                    # doesn't strand in inflight until restart (the old
                    # behaviour silently dropped the user's utterance). The
                    # idempotency claim token, if set, suppresses duplicates.
                    logger.exception("whisper.loop_exception error=%s", e)
                    if raw_value is not None:
                        try:
                            path_info = json.loads(raw_value)
                            attempt = int(path_info.get("attempt", 1) or 1)
                            if attempt < self._max_attempts:
                                path_info["attempt"] = attempt + 1
                                path_info["last_error"] = f"loop_exception: {e}"
                                moved = await asyncio.to_thread(
                                    redis_client.eval,
                                    _RETRY_MOVE_LUA,
                                    2,
                                    WHISPER_INFLIGHT_QUEUE,
                                    WHISPER_QUEUE,
                                    raw_value,
                                    json.dumps(path_info),
                                )
                                if not moved:
                                    # Job wasn't in inflight (e.g. already
                                    # acked); plain requeue.
                                    await asyncio.to_thread(
                                        redis_client.lpush,
                                        WHISPER_QUEUE,
                                        json.dumps(path_info),
                                    )
                            else:
                                path_info["attempt"] = attempt
                                path_info["dead_reason"] = "loop_exception"
                                await asyncio.to_thread(
                                    redis_client.lpush,
                                    WHISPER_DEAD_QUEUE,
                                    json.dumps(path_info),
                                )
                                await asyncio.to_thread(
                                    redis_client.lrem,
                                    WHISPER_INFLIGHT_QUEUE,
                                    1,
                                    raw_value,
                                )
                        except Exception:
                            logger.exception(
                                "whisper.requeue_after_error_failed — job may be "
                                "stranded until the idle reaper runs"
                            )
                    await asyncio.sleep(0.25)


async def _amain(worker: "WhisperWorker"):
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(worker.process_audio())
    # systemd sends SIGTERM; ./kill sends TERM/INT. Cancel the loop instead of
    # dying mid-job (the inflight entry is then requeued by the idle reaper).
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("whisper worker stopped by signal.")


def main():
    worker = WhisperWorker()
    asyncio.run(_amain(worker))


if __name__ == "__main__":
    main()
