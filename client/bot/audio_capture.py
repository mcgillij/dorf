"""AudioCapture class to capture and save audio per user."""

import os
import json
import wave
import time
from pydub import AudioSegment
import discord
from discord.ext.voice_recv import AudioSink, VoiceData
import redis
from dotenv import load_dotenv
from .utilities import RingBuffer, logger
from typing import Dict, Optional
import asyncio


load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class RingBufferAudioSink(AudioSink):
    def __init__(self, bot, buffer_size=1024 * 1024, output_dir="user_audio"):
        self.bot = bot  # Store bot instance for access to the loop
        self.ring_buffers = {}
        self.buffer_size = buffer_size
        self.output_dir = output_dir
        self.last_check_time = {}
        self.last_audio_time: Dict[int, float] = {}
        self.processing_locks: Dict[int, asyncio.Lock] = {}
        self.save_task = None
        self.ssrc_to_user: Dict[int, int] = {}  # Map SSRC to user ID
        os.makedirs(self.output_dir, exist_ok=True)
        logger.info("RingBufferAudioSink initialized")

    def write(self, member, data: VoiceData):
        try:
            current_time = time.time()
            user_id = member.id if member else None
            if not user_id:
                return

            if user_id not in self.processing_locks:
                self.processing_locks[user_id] = asyncio.Lock()

            if user_id not in self.ring_buffers:
                logger.info(f"Creating new buffer for user {user_id}")
                self.ring_buffers[user_id] = RingBuffer(self.buffer_size)
                self.last_check_time[user_id] = current_time

            self.ring_buffers[user_id].write(data.pcm)
            self.last_audio_time[user_id] = current_time

            # Use the bot's loop
            if not self.save_task or self.save_task.done():
                self.save_task = asyncio.run_coroutine_threadsafe(
                    self.check_for_silence(), self.bot.loop
                )

        except Exception as e:
            logger.error(f"Error in write method: {e}")

    def on_voice_state_update(self, member, state):
        """Called when a member's voice state changes"""
        if member and hasattr(state, "ssrc"):
            self.ssrc_to_user[state.ssrc] = member.id
            logger.info(f"Mapped SSRC {state.ssrc} to user {member.id}")

    async def check_for_silence(self):
        """Background task to check for silence periods and save audio"""
        try:
            while True:
                current_time = time.time()
                for user_id, last_time in list(self.last_audio_time.items()):
                    # If we haven't received audio for 0.5 seconds (adjust as needed)
                    if current_time - last_time > 0.5:
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
                redis_client.lpush(
                    "whisper_queue",
                    json.dumps({"user_id": user_id, "audio_path": converted_path}),
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
        original_path = os.path.join(output_dir, f"{user_id}-original.wav")
        converted_path = os.path.join(output_dir, f"{user_id}.wav")

        logger.info(f"Saving original audio to {original_path}")
        with wave.open(original_path, "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(48000)
            wav_file.writeframes(pcm_data)

        logger.info("Converting audio to Whisper format")
        audio = AudioSegment.from_file(original_path, format="wav")
        audio.set_channels(1).set_frame_rate(16000).export(
            converted_path, format="wav", codec="pcm_s16le"
        )

        os.remove(original_path)
        logger.info(f"Successfully saved and converted audio to {converted_path}")
        return converted_path
    except Exception as e:
        logger.error(f"Error in save_audio: {e}")
        return None


class VoiceRecvClient(discord.VoiceProtocol):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        logger.info("VoiceRecvClient init")
        super().__init__(client, channel)
        self.audio_sink = None

    async def on_ready(self):
        logger.info("VoiceRecvClient on_ready")
        if self.audio_sink:
            await self.send_audio_packet(b"", True)

    async def send_audio_packet(self, data, is_last=False):
        logger.info(f"Sending audio packet to sink: {self.audio_sink}")
        if not self.audio_sink:
            return
        await self.audio_sink.read(data)
