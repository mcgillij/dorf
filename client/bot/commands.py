import discord
from discord.ext import commands, voice_recv
from discord.ext.voice_recv import VoiceData
from bot.utilities import split_message, split_text
import redis
import json
import hashlib
import asyncio
import os
import numpy as np
import wave

# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = os.getenv("REDIS_PORT", "")
redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)

# Configure bot and intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.members = True
bot = commands.Bot(command_prefix='!', intents=INTENTS)

LONG_RESPONSE_THRESHOLD = 1000
# Context dictionary
context_dict = {}

# Helper functions
def generate_unique_id(ctx, message: str) -> str:
    """Generates a unique ID based on context and message."""
    return hashlib.md5(f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()).hexdigest()

async def poll_redis_for_key(key: str, timeout: float = 0.5) -> str:
    """Polls Redis for a key and returns its value when found."""
    while True:
        response = redis_client.get(key)
        if response:
            redis_client.delete(key)
            return response.decode('utf-8') if isinstance(response, bytes) else response
        await asyncio.sleep(timeout)

async def process_audio_queue(unique_id: str, messages: list[str], voice_user_count: int):
    """Queues messages for audio generation if users are in the voice channel."""
    if voice_user_count > 0:
        index = 1
        for msg in messages:
            redis_client.lpush('audio_queue', f"{unique_id}|{index}|{msg}")
            index += 1

@bot.command()
async def derf(ctx, *, message: str):
    unique_id = generate_unique_id(ctx, message)
    print(f"Unique ID: {unique_id}")

    # Store the context if not already stored
    context_dict.setdefault(unique_id, ctx)

    # Queue the message for processing
    redis_client.lpush('response_queue', json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}))

    # Poll Redis for the result
    response = await poll_redis_for_key(f"response:{unique_id}")

    # Send the response in chunks
    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)

    # Check for voice channel users
    voice_user_count = len(ctx.guild.voice_client.channel.members) - 1 if ctx.guild.voice_client else 0
    print(f"Number of users in voice channel: {voice_user_count}")

    # Summarize response if it's long
    if len(response) > LONG_RESPONSE_THRESHOLD:
        redis_client.lpush('summarizer_queue', json.dumps({"unique_id": unique_id, "message": response}))
        summary_response = await poll_redis_for_key(f"summarizer:{unique_id}")

        await ctx.send(summary_response)
        await process_audio_queue(unique_id, split_text(summary_response), voice_user_count)
    else:
        await process_audio_queue(unique_id, split_text(response), voice_user_count)

class AudioCapture(voice_recv.AudioSink):
    """
    A custom audio sink to capture and save audio per user.
    """
    def __init__(self, output_dir="audio"):
        self.output_dir = output_dir
        self.user_audio_data = {}

        # Ensure the output directory exists
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def write(self, member, data: VoiceData):
        """
        Collects PCM audio data for each user.
        Args:
            member: The user whose audio is being processed.
            data: The VoiceData object containing PCM audio.
        """
        pcm_bytes = data.pcm  # Extract the raw PCM data from VoiceData
        if member.id not in self.user_audio_data:
            self.user_audio_data[member.id] = []
        self.user_audio_data[member.id].append(pcm_bytes)

    def save(self):
        """
        Saves captured audio for each user to separate .wav files.
        """
        if not self.user_audio_data:
            print("No audio data to save.")
            return

        for user_id, audio_data in self.user_audio_data.items():
            if not audio_data:
                print(f"No audio data for user {user_id}.")
                continue

            pcm_data = b"".join(audio_data)
            np_data = np.frombuffer(pcm_data, dtype=np.int16)

            output_path = os.path.join(self.output_dir, f"{user_id}.wav")
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(2)  # stereo
                wav_file.setsampwidth(2)  # 16-bit PCM
                wav_file.setframerate(48000)  # Discord uses 48kHz sample rate
                wav_file.writeframes(np_data.tobytes())

            print(f"Saved audio for user {user_id} to {output_path}.")

    def cleanup(self):
        """
        Clean up resources if needed.
        """
        pass

    def wants_opus(self):
        """
        Return False because we want PCM audio data, not Opus.
        """
        return False

@bot.command()
async def capture(ctx):
    """
    Starts capturing audio for all users in the voice channel.
    """
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel to use this command.")
        return

    voice_channel = ctx.author.voice.channel
    vc = ctx.guild.voice_client

    if not vc:
        vc = await voice_channel.connect(cls=voice_recv.VoiceRecvClient)

    if vc.is_listening():
        await ctx.send("Already capturing audio.")
        return

    audio_sink = AudioCapture(output_dir="user_audio")
    vc.listen(audio_sink)

    await ctx.send("Recording started. Use `!stopcapture` to stop and save the audio.")

@bot.command()
async def stop(ctx):
    """
    Stops capturing audio and saves it to files.
    """
    vc = ctx.guild.voice_client

    if vc and vc.is_listening():
        audio_sink = vc.sink
        vc.stop_listening()
        audio_sink.save()
        await ctx.send("Recording stopped and audio saved.")
    else:
        await ctx.send("The bot is not currently recording.")
