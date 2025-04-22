import asyncio
import redis
import os
import discord
from bot.client import derf_bot, nic_bot
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)
load_dotenv()

VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", ""))

redis_client = redis.Redis(host="0.0.0.0", port=6379, decode_responses=True)


async def playback_task(bot_instance, queue_name, voice_channel_id):
    """
    Base function to process playback requests from a Redis queue.
    """
    while True:
        try:
            playback_data = redis_client.rpop(queue_name)
            if not playback_data:
                await asyncio.sleep(1)
                continue

            # Parse the playback data
            unique_id, opus_path = playback_data.split("|", 1)

            # Fetch the voice channel by ID
            channel = bot_instance.get_channel(voice_channel_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                logger.info(f"Voice channel {voice_channel_id} not found or invalid.")
                continue

            # Get the voice client for the guild
            guild = channel.guild
            voice_client = discord.utils.get(bot_instance.voice_clients, guild=guild)

            if not voice_client or not voice_client.is_connected():
                logger.info("Voice client not connected. Attempting to reconnect...")
                try:
                    voice_client = await channel.connect()
                except discord.ClientException as e:
                    logger.error(f"Error connecting to voice channel: {e}")
                    continue

            # Check to see if there's any humans in the channel
            has_humans = any(not member.bot for member in channel.members)

            if not has_humans:
                logger.info(
                    f"Skipping playback as there are only bots in {channel.name}."
                )
                continue  # Skip to the next iteration

            # Play the generated audio
            audio_source = await discord.FFmpegOpusAudio.from_probe(
                opus_path, method="fallback", options="-vn -b:a 128k"
            )
            voice_client.play(
                audio_source,
                after=lambda e: logger.error(f"Player error: {e}") if e else None,
            )

            # Wait for the audio to finish playing
            while voice_client.is_playing():
                await asyncio.sleep(0.1)

            # Clean up the opus file
            if os.path.exists(opus_path):
                os.remove(opus_path)

        except Exception as e:
            logger.error(f"Error in playback_task_base: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors


async def playback_derf_task():
    """
    Wrapper for the playback task for the main bot.
    """
    await playback_task(derf_bot, "playback_queue", VOICE_CHANNEL_ID)


async def playback_nic_task():
    """
    Wrapper for the playback task for the nic_bot.
    """
    await playback_task(nic_bot, "playback_nic_queue", VOICE_CHANNEL_ID)
