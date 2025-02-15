import asyncio
import redis
import os
import discord
from bot.commands import bot, context_dict

from dotenv import load_dotenv

load_dotenv()

VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", ""))

redis_client = redis.Redis(host="0.0.0.0", port=6379, decode_responses=True)


async def playback_task():
    """
    Continuously process playback requests from the Redis queue.
    """

    while True:
        try:
            playback_data = redis_client.rpop("playback_queue")
            if not playback_data:
                await asyncio.sleep(1)
                continue

            # Parse the playback data
            unique_id, opus_path = playback_data.split("|", 1)

            # Fetch the voice channel by ID
            channel = bot.get_channel(VOICE_CHANNEL_ID)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                print(f"Voice channel {VOICE_CHANNEL_ID} not found or invalid.")
                continue

            # Get the voice client for the guild
            guild = channel.guild
            voice_client = discord.utils.get(bot.voice_clients, guild=guild)

            if not voice_client or not voice_client.is_connected():
                print("Voice client not connected. Attempting to reconnect...")
                try:
                    voice_client = await channel.connect()
                except discord.ClientException as e:
                    print(f"Error connecting to voice channel: {e}")
                    continue

            # Check the number of users in the voice channel
            num_users = len(channel.members) - 1  # Subtract 1 for the bot itself
            if num_users < 1:
                print(
                    f"Skipping playback as there are only {num_users} users in the voice channel."
                )
                continue

            # Play the generated audio
            audio_source = await discord.FFmpegOpusAudio.from_probe(
                opus_path, method="fallback", options="-vn -b:a 128k"
            )
            voice_client.play(
                audio_source, after=lambda e: print(f"Player error: {e}") if e else None
            )

            # Wait for the audio to finish playing
            while voice_client.is_playing():
                await asyncio.sleep(0.1)

            # Clean up the opus file
            if os.path.exists(opus_path):
                os.remove(opus_path)

        except Exception as e:
            print(f"Error in playback_task: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors
