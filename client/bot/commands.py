import discord
from discord.ext import commands
from bot.utilities import split_message
import redis
import json
import hashlib
import asyncio
import os

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
        await process_audio_queue(unique_id, summary_response.split("\n"), voice_user_count)
    else:
        await process_audio_queue(unique_id, response.split("\n"), voice_user_count)

