import os
import tempfile
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pydub import AudioSegment

from bot.processing import redis_client
from bot.workers.stop_flags import stop_requested
from bot.tts import synthesize
from bot.constants import (
    DERF_AUDIO_QUEUE,
    DERF_PLAYBACK_QUEUE,
    NIC_AUDIO_QUEUE,
    NIC_PLAYBACK_QUEUE,
    TTS_PROVIDER,
    TTS_VOICE,
    TTS_VOICE_NICOLE,
)

logger = logging.getLogger(__name__)

# Kokoro + audio conversion get a dedicated executor so they can't starve
# (or be starved by) redis ops and capture flushes on the default pool.
_TTS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts")


def convert_wav_to_opus(wav_path, opus_path):
    """Sync function to convert WAV to OPUS."""
    audio_segment = AudioSegment.from_wav(wav_path)
    audio_segment.export(opus_path, format="opus", parameters=["-b:a", "128k"])


async def audio_task(queue_name, playback_queue_name, tts_voice, bot_instance):
    # Repo-root relative by default; override for deploys that run elsewhere.
    output_dir = os.getenv("AUDIO_OUTPUT_DIR", "output")
    os.makedirs(output_dir, exist_ok=True)
    loop = asyncio.get_event_loop()  # Reuse the same event loop
    # Log the configured provider name (not get_provider(), which would
    # eagerly import the backend — torch weight — at worker start; the lazy
    # import must happen on the first real synth instead).
    logger.info(
        "audio_worker.start queue=%s playback_queue=%s voice=%s provider=%s "
        "output_dir=%s",
        queue_name,
        playback_queue_name,
        tts_voice,
        TTS_PROVIDER,
        output_dir,
    )
    while True:
        try:
            task_data = await loop.run_in_executor(None, redis_client.rpop, queue_name)

            if not task_data:
                await asyncio.sleep(1)
                continue

            logger.info(
                "audio_worker.dequeue queue=%s playback_queue=%s bytes=%s",
                queue_name,
                playback_queue_name,
                len(task_data) if isinstance(task_data, str) else -1,
            )

            unique_id, line_number, line_text = task_data.split("|", 2)

            # If a recent stop/shutup was issued for this channel, drop pending speech.
            persona = "nic" if queue_name == NIC_AUDIO_QUEUE else "derf"
            if await stop_requested(bot_instance, persona):
                logger.info(
                    "audio_worker.dropped_due_to_stop unique_id=%s persona=%s queue=%s",
                    unique_id,
                    persona,
                    queue_name,
                )
                continue

            if bot_instance.voice_clients and bot_instance.voice_clients[0].channel:
                channel = bot_instance.voice_clients[0].channel
                member_count = len(channel.members)
                # Both bots sit in the channel; counting `member_count - 1`
                # assumed one bot and reported one too many humans.
                num_users = sum(1 for m in channel.members if not m.bot)
            else:
                channel = None
                member_count = 0
                num_users = 0

            if num_users < 1:
                logger.info(
                    "audio_worker.skip_no_humans unique_id=%s users=%s member_count=%s channel=%s queue=%s",
                    unique_id,
                    num_users,
                    member_count,
                    getattr(channel, "name", None),
                    queue_name,
                )
                continue

            # Ensure unique artifacts per request to avoid cross-talk / overwrites.
            wav_path = os.path.join(output_dir, f"{unique_id}-{line_number}.wav")

            try:
                await loop.run_in_executor(
                    _TTS_EXECUTOR,
                    synthesize,
                    line_text,
                    tts_voice,
                    wav_path,
                )
            except Exception as e:
                logger.exception(
                    "audio_worker.tts_error unique_id=%s voice=%s err=%s",
                    unique_id,
                    tts_voice,
                    e,
                )
                continue

            if not os.path.exists(wav_path):
                logger.error(f"WAV missing: {wav_path}")
                continue

            # Convert to OPUS
            with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as tmp_opus:
                opus_path = tmp_opus.name

                try:
                    await loop.run_in_executor(
                        _TTS_EXECUTOR, convert_wav_to_opus, wav_path, opus_path
                    )
                except Exception:
                    # Don't leak the empty temp file if conversion failed.
                    try:
                        if os.path.exists(opus_path):
                            os.remove(opus_path)
                    except OSError:
                        logger.warning(
                            "audio_worker.opus_cleanup_failed opus_path=%s",
                            opus_path,
                            exc_info=True,
                        )
                    raise

                # Best-effort cleanup of intermediate wav
                try:
                    if os.path.exists(wav_path):
                        os.remove(wav_path)
                except Exception:
                    pass

                # Re-check stop before enqueuing playback (covers in-flight TTS).
                if await stop_requested(bot_instance, persona):
                    logger.info(
                        "audio_worker.skip_enqueue_due_to_stop unique_id=%s persona=%s opus_path=%s",
                        unique_id,
                        persona,
                        opus_path,
                    )
                    try:
                        if os.path.exists(opus_path):
                            os.remove(opus_path)
                    except OSError:
                        logger.warning(
                            "audio_worker.opus_cleanup_failed opus_path=%s",
                            opus_path,
                            exc_info=True,
                        )
                    continue

                # Push to playback queue without blocking
                await loop.run_in_executor(
                    None,
                    redis_client.lpush,
                    playback_queue_name,
                    f"{unique_id}|{opus_path}",
                )

                logger.info(
                    "audio_worker.enqueued_playback unique_id=%s playback_queue=%s opus_path=%s",
                    unique_id,
                    playback_queue_name,
                    opus_path,
                )

        except Exception as e:
            logger.exception("audio_worker.loop_error queue=%s err=%s", queue_name, e)
            await asyncio.sleep(1)


async def nic_audio_task(bot):
    await audio_task(
        queue_name=NIC_AUDIO_QUEUE,
        playback_queue_name=NIC_PLAYBACK_QUEUE,
        tts_voice=TTS_VOICE_NICOLE,
        bot_instance=bot,
    )


async def derf_audio_task(bot):
    await audio_task(
        queue_name=DERF_AUDIO_QUEUE,
        playback_queue_name=DERF_PLAYBACK_QUEUE,
        tts_voice=TTS_VOICE,
        bot_instance=bot,
    )
