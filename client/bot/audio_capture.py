"""AudioCapture class to capture and save audio per user."""

import os
import json
import wave
import time
import threading
from typing import Dict
import asyncio
import logging
import uuid

from pydub import AudioSegment
import discord
from discord.ext.voice_recv import AudioSink, VoiceData
from bot.redis_client import redis_client
from bot.constants import WHISPER_QUEUE

logger = logging.getLogger(__name__)


class RingBuffer:
    def __init__(self, size: int):
        self.buffer = bytearray(size)
        self.size = size
        self.write_ptr = 0
        self.read_ptr = 0
        self.is_full = False
        self.lock = threading.Lock()

    def write(self, data: bytes):
        with self.lock:
            data_len = len(data)
            if data_len > self.size:
                # If data exceeds buffer size, write only the last chunk
                data = data[-self.size :]
                data_len = len(data)

            # Write data in a circular manner
            for byte in data:
                self.buffer[self.write_ptr] = byte
                self.write_ptr = (self.write_ptr + 1) % self.size
                if self.is_full:
                    self.read_ptr = (self.read_ptr + 1) % self.size
                self.is_full = self.write_ptr == self.read_ptr

    def read_all(self) -> bytes:
        with self.lock:
            if not self.is_full and self.write_ptr == self.read_ptr:
                # Buffer is empty
                return b""

            if self.is_full:
                # Read from the full buffer
                data = self.buffer[self.read_ptr :] + self.buffer[: self.write_ptr]
            else:
                # Read from the used portion
                data = self.buffer[self.read_ptr : self.write_ptr]

            self.read_ptr = self.write_ptr  # Mark buffer as read
            self.is_full = False
            return bytes(data)

    def is_empty(self) -> bool:
        with self.lock:
            return not self.is_full and self.write_ptr == self.read_ptr

    def clear(self):
        with self.lock:
            self.write_ptr = 0
            self.read_ptr = 0
            self.is_full = False


class RingBufferAudioSink(AudioSink):
    def __init__(
        self,
        bot,
        buffer_size=1024 * 1024,
        output_dir="user_audio",
        silence_seconds: float = 0.3,
        max_chunk_seconds: float = 10.0,
    ):
        self.bot = bot  # Store bot instance for access to the loop
        self.ring_buffers = {}
        self.buffer_size = buffer_size
        self.output_dir = output_dir
        self.silence_seconds = silence_seconds
        self.max_chunk_seconds = max_chunk_seconds
        self.last_check_time = {}
        self.last_audio_time: Dict[int, float] = {}
        self.last_packet_time: float = 0.0
        self.chunk_start_time: Dict[int, float] = {}
        self.user_context: Dict[int, Dict[str, int]] = {}
        self.processing_locks: Dict[int, asyncio.Lock] = {}
        self.save_task = None
        self.ssrc_to_user: Dict[int, int] = {}  # Map SSRC to user ID
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info("RingBufferAudioSink initialized")

    def write(self, member, data: VoiceData):
        try:
            current_time = time.time()
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
                self.last_check_time[user_id] = current_time
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
                current_time = time.time()
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
                        if (
                            user_id in self.ring_buffers
                            and not self.ring_buffers[user_id].is_empty()
                        ):
                            # Only process if we're not already processing for this user
                            if not self.processing_locks[user_id].locked():
                                async with self.processing_locks[user_id]:
                                    await self.bot.loop.run_in_executor(
                                        None, self.save_user_audio, user_id
                                    )
                            del self.last_audio_time[user_id]
                            self.chunk_start_time[user_id] = current_time

                # If no active audio streams, end the task
                if not self.last_audio_time:
                    return

                await asyncio.sleep(0.1)  # Small delay to prevent CPU overuse
        except Exception as e:
            logger.error(f"Error in check_for_silence: {e}")

    def save_user_audio(self, user_id):
        try:
            logger.info(f"Attempting to save audio for user {user_id}")
            ring_buffer = self.ring_buffers.get(user_id)
            if not ring_buffer:
                logger.info(f"No ring buffer found for user {user_id}")
                return
            pcm_data = ring_buffer.read_all()
            if pcm_data:
                logger.info(f"Got PCM data of length {len(pcm_data)}")
                converted_path = save_audio(user_id, pcm_data, self.output_dir)
                if not converted_path:
                    logger.error("Failed to save/convert audio; skipping enqueue.")
                    ring_buffer.clear()
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
                    ring_buffer.clear()
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
            else:
                logger.error("No PCM data to save")
            ring_buffer.clear()
        except Exception as e:
            logger.error(f"Error in save_user_audio: {e}")

    def save(self):
        logger.info("Manual save triggered")
        for user_id in list(self.ring_buffers.keys()):
            self.save_user_audio(user_id)

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


class VoiceRecvClient(discord.VoiceProtocol):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        logger.info("VoiceRecvClient init")
        super().__init__(client, channel)
        self.audio_sink = None
