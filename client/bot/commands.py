import json
import hashlib
import asyncio
import os

import redis
import discord
from discord.ext import commands, voice_recv
from dotenv import load_dotenv

from bot.utilities import split_message, split_text
from bot.audio_capture import RingBufferAudioSink, VoiceRecvClient

load_dotenv()
# Configure Redis
REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = int(os.getenv("REDIS_PORT", ""))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

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
@bot.event
async def on_voice_state_update(member, before, after):
    print(f"Voice state update: {member} | Before: {before.channel} | After: {after.channel}")

    # Check if the member is joining a voice channel
    if after.channel and not before.channel:
        await start_capture(member.guild, after.channel)

# Function to start capturing audio when a user joins a voice channel
async def start_capture(guild, channel):
    vc = guild.voice_client

    if not vc or vc.channel != channel:
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)

    if vc.is_listening():
        print("Already capturing audio.")
        return

    ring_buffer_sink = RingBufferAudioSink(buffer_size=1024 * 1024)
    vc.listen(ring_buffer_sink)

    print(f"Recording started in channel {channel}. Use `!stop_capture` to stop and save the audio.")


@bot.command()
async def cap(ctx):
    """
    Starts capturing audio for all users in the voice channel using the ring buffer.
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

    ring_buffer_sink = RingBufferAudioSink(buffer_size=1024 * 1024)
    vc.listen(ring_buffer_sink)

    await ctx.send("Recording started. Use `!stop_capture` to stop and save the audio.")

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
        audio_sink.cleanup2()
        await ctx.send("Recording stopped and audio saved.")
    else:
        await ctx.send("The bot is not currently recording.")

@bot.command()
async def test_whisper(ctx):
    """
    test whisper
    """
    from .utilities import WhisperClient
    whisper_client = WhisperClient()
    text = await whisper_client.get_text("user_audio/427590626905948165.wav")
    await ctx.send(f"Here is your whisper text: {text}.")
