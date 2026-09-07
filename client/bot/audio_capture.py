"""AudioCapture class to capture and save audio per user."""

import os
import json
import wave
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
import asyncio
import logging
import uuid

from pydub import AudioSegment
from discord.ext.voice_recv import AudioSink, VoiceData
from bot.redis_client import redis_client
from bot.constants import WHISPER_QUEUE

logger = logging.getLogger(__name__)

# Capture flushes (WAV write + ffmpeg convert) get a dedicated executor so long
# TTS/redis jobs on the default executor can't starve them and stall flushes.
_FLUSH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="capture-flush")


class RingBuffer:
    """Thread-safe circular byte buffer.

    Invariant: unread bytes live at [read_ptr, read_ptr + available) (wrapping),
    i.e. read_ptr == (write_ptr - available) % size at all times. Writes that
    would overflow capacity discard the oldest unread bytes first, so reads are
    always chronological.
    """

    def __init__(self, size: int):
        self.buffer = bytearray(size)
        self.size = size
        self.write_ptr = 0
        self.read_ptr = 0
        self.available = 0
        self.lock = threading.Lock()

    def write(self, data: bytes):
        n = len(data)
        with self.lock:
            if n > self.size:
                # Keep only the newest `size` bytes.
                data = data[-self.size :]
                n = self.size
            # Discard oldest unread bytes the write would overwrite.
            discard = max(0, self.available + n - self.size)
            self.available = self.available + n - discard
            end = self.write_ptr + n
            if end <= self.size:
                self.buffer[self.write_ptr : end] = data
            else:
                first = self.size - self.write_ptr
                self.buffer[self.write_ptr :] = data[:first]
                self.buffer[: end - self.size] = data[first:]
            self.write_ptr = end % self.size
            self.read_ptr = (self.write_ptr - self.available) % self.size

    def _read_locked(self, reset_ptrs: bool) -> bytes:
        if self.available == 0:
            return b""
        if self.read_ptr + self.available <= self.size:
            data = bytes(self.buffer[self.read_ptr : self.read_ptr + self.available])
        else:
            first = self.size - self.read_ptr
            data = bytes(self.buffer[self.read_ptr :]) + bytes(
                self.buffer[: self.available - first]
            )
        if reset_ptrs:
            self.read_ptr = 0
            self.write_ptr = 0
        else:
            self.read_ptr = self.write_ptr
        self.available = 0
        return data

    def read_all(self) -> bytes:
        """Read all unread bytes and mark them read."""
        with self.lock:
            return self._read_locked(reset_ptrs=False)

    def drain(self) -> bytes:
        """Atomically read everything and reset the buffer.

        Audio written while a chunk is being converted lands in a fresh buffer
        instead of being wiped by a post-conversion clear().
        """
        with self.lock:
            return self._read_locked(reset_ptrs=True)

    def is_empty(self) -> bool:
        with self.lock:
            return self.available == 0

    def clear(self):
        with self.lock:
            self.write_ptr = 0
            self.read_ptr = 0
            self.available = 0


class RingBufferAudioSink(AudioSink):
    def __init__(
        self,
        bot,
        buffer_size=1024 * 1024,
        output_dir="user_audio",
        silence_seconds: float = 0.5,
        max_chunk_seconds: float = 10.0,
    ):
        self.bot = bot  # Store bot instance for access to the loop
        self.ring_buffers = {}
        self.buffer_size = buffer_size
        self.output_dir = output_dir
        self.silence_seconds = silence_seconds
        self.max_chunk_seconds = max_chunk_seconds
        self.last_audio_time: Dict[int, float] = {}
        self.last_packet_time: float = 0.0
        self.chunk_start_time: Dict[int, float] = {}
        self.user_context: Dict[int, Dict[str, int]] = {}
        self.processing_locks: Dict[int, asyncio.Lock] = {}
        self.save_task = None
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info("RingBufferAudioSink initialized")

    def write(self, member, data: VoiceData):
        try:
            current_time = time.monotonic()
            self.last_packet_time = current_time
            # Never record bots (prevents bots recording each other / themselves).
            if member is not None and getattr(member, "bot", False):
                return
            user_id = member.id if member else None
            if not user_id:
                return

            # Capture context for downstream routing/observability
            try:
                if member and member.guild and member.voice and member.voice.channel:
                    self.user_context[user_id] = {
                        "guild_id": member.guild.id,
                        "channel_id": member.voice.channel.id,
                    }
            except Exception:
                # Best-effort only
                pass

            if user_id not in self.processing_locks:
                self.processing_locks[user_id] = asyncio.Lock()

            if user_id not in self.ring_buffers:
                logger.info(f"Creating new buffer for user {user_id}")
                self.ring_buffers[user_id] = RingBuffer(self.buffer_size)
                self.chunk_start_time[user_id] = current_time

            self.ring_buffers[user_id].write(data.pcm)
            self.last_audio_time[user_id] = current_time

            # Use the bot's loop
            if not self.save_task or self.save_task.done():
                self.save_task = asyncio.run_coroutine_threadsafe(
                    self.check_for_silence(), self.bot.loop
                )

        except Exception as e:
            logger.error(f"Error in write method: {e}")

    async def check_for_silence(self):
        """Background task to check for silence periods and save audio"""
        try:
            while True:
                current_time = time.monotonic()
                for user_id, last_time in list(self.last_audio_time.items()):
                    silence_elapsed = current_time - last_time
                    chunk_elapsed = current_time - self.chunk_start_time.get(
                        user_id, last_time
                    )

                    # Flush on silence or on max duration (handles continuous speech)
                    if (
                        silence_elapsed > self.silence_seconds
                        or chunk_elapsed > self.max_chunk_seconds
                    ):
                        lock = self.processing_locks.get(user_id)
                        if lock is None or lock.locked():
                            # A flush is already running for this user; retry
                            # on the next tick instead of racing it.
                            continue
                        async with lock:
                            await self.bot.loop.run_in_executor(
                                _FLUSH_EXECUTOR, self.save_user_audio, user_id
                            )
                        # New chunks start from now, regardless of how long the
                        # save took.
                        self.chunk_start_time[user_id] = time.monotonic()
                        # Only stop tracking the user when no audio arrived while
                        # we were saving; otherwise their new packets are still
                        # pending in a fresh buffer and must be flushed later.
                        # (pop: the user may have left voice and been forgotten
                        # while the save was running.)
                        if self.last_audio_time.get(user_id) == last_time:
                            self.last_audio_time.pop(user_id, None)

                # If no active audio streams, end the task
                if not self.last_audio_time:
                    return

                await asyncio.sleep(0.1)  # Small delay to prevent CPU overuse
        except Exception as e:
            logger.error(f"Error in check_for_silence: {e}")

    def forget_user(self, user_id) -> None:
        """Drop all per-user state when a member leaves the voice channel.

        Without this, every departed user leaks a full-size ring buffer and a
        lock for the lifetime of the sink.
        """
        self.ring_buffers.pop(user_id, None)
        self.processing_locks.pop(user_id, None)
        self.user_context.pop(user_id, None)
        self.last_audio_time.pop(user_id, None)
        self.chunk_start_time.pop(user_id, None)
        logger.info(f"Forgot capture state for user {user_id}")

    def save_user_audio(self, user_id):
        try:
            logger.info(f"Attempting to save audio for user {user_id}")
            ring_buffer = self.ring_buffers.get(user_id)
            if not ring_buffer:
                logger.info(f"No ring buffer found for user {user_id}")
                return
            # Atomic drain: packets that arrive during the conversion below are
            # written to a fresh buffer and survive for the next flush.
            pcm_data = ring_buffer.drain()
            if not pcm_data:
                logger.info(f"No PCM data to save for user {user_id}")
                return
            logger.info(f"Got PCM data of length {len(pcm_data)}")
            converted_path = save_audio(user_id, pcm_data, self.output_dir)
            if not converted_path:
                logger.error("Failed to save/convert audio; skipping enqueue.")
                return

            payload = {
                "user_id": user_id,
                "audio_path": converted_path,
                "trace_id": str(uuid.uuid4()),
            }
            payload.update(self.user_context.get(user_id, {}))
            payload_json = json.dumps(payload)
            try:
                new_len = redis_client.lpush(WHISPER_QUEUE, payload_json)
            except Exception as e:
                logger.error(
                    "whisper.enqueue_failed user_id=%s path=%s error=%s",
                    user_id,
                    converted_path,
                    e,
                )
                return

            logger.info(
                "whisper.enqueue_ok user_id=%s trace_id=%s guild_id=%s channel_id=%s queue=%s new_len=%s payload_bytes=%s path=%s",
                user_id,
                payload.get("trace_id"),
                payload.get("guild_id"),
                payload.get("channel_id"),
                WHISPER_QUEUE,
                new_len,
                len(payload_json.encode("utf-8")),
                converted_path,
            )

            logger.info(f"Saved audio to {converted_path}")
        except Exception as e:
            logger.error(f"Error in save_user_audio: {e}")

    def cleanup(self):
        pass

    def wants_opus(self):
        return False


def save_audio(user_id: int, pcm_data, output_dir: str) -> str:
    try:
        os.makedirs(output_dir, exist_ok=True)
        user_dir = os.path.join(output_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        chunk_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        original_path = os.path.join(user_dir, f"chunk-{chunk_id}-original.wav")
        converted_path = os.path.join(user_dir, f"chunk-{chunk_id}.wav")

        logger.info(
            "save_audio: user_id=%s pcm_bytes=%s original_path=%s converted_path=%s",
            user_id,
            len(pcm_data) if pcm_data else 0,
            original_path,
            converted_path,
        )

        logger.info(f"Saving original audio to {original_path}")
        with wave.open(original_path, "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48000)
            wav_file.writeframes(pcm_data)

        try:
            original_size = os.path.getsize(original_path)
        except OSError:
            original_size = None

        logger.info(
            "save_audio: wrote original wav size_bytes=%s channels=%s sample_width=%s framerate=%s",
            original_size,
            2,
            2,
            48000,
        )

        logger.info(
            "Converting audio to Whisper format (pydub converter=%s ffmpeg=%s)",
            getattr(AudioSegment, "converter", None),
            getattr(AudioSegment, "ffmpeg", None),
        )
        audio = AudioSegment.from_file(original_path, format="wav")
        logger.info(
            "save_audio: decoded original duration_ms=%s channels=%s frame_rate=%s sample_width=%s dBFS=%s rms=%s",
            len(audio),
            audio.channels,
            audio.frame_rate,
            audio.sample_width,
            getattr(audio, "dBFS", None),
            getattr(audio, "rms", None),
        )

        converted_audio = audio.set_channels(1).set_frame_rate(16000)
        logger.info(
            "save_audio: converted target duration_ms=%s channels=%s frame_rate=%s sample_width=%s",
            len(converted_audio),
            converted_audio.channels,
            converted_audio.frame_rate,
            converted_audio.sample_width,
        )

        converted_audio.export(converted_path, format="wav", codec="pcm_s16le")

        try:
            converted_size = os.path.getsize(converted_path)
        except OSError:
            converted_size = None

        logger.info(
            "save_audio: wrote converted wav size_bytes=%s path=%s",
            converted_size,
            converted_path,
        )

        os.remove(original_path)
        logger.info(f"Successfully saved and converted audio to {converted_path}")
        return converted_path
    except Exception as e:
        logger.exception(f"Error in save_audio: {e}")
        return None
