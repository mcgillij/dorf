import discord
from discord.ext import commands
from bot.utilities import split_message
import redis
import json
import hashlib
from random import randint
import asyncio


context_dict = {}

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True

redis_client = redis.Redis(host='0.0.0.0', port=6379, decode_responses=True)

bot = commands.Bot(command_prefix='!', intents=INTENTS)

@bot.command()
async def derf(ctx, *, message: str):
    unique_id = hashlib.md5(f"{ctx.guild.id}^{ctx.channel.id}^{ctx.author.id}^{message}".encode()).hexdigest()
    print(f"Unique ID: {unique_id}")

    # Store the ctx object in the dictionary
    if unique_id not in context_dict:
        context_dict[unique_id] = ctx

    # Queue the message for processing
    redis_client.lpush('response_queue', json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}))

    # Poll Redis for the result
    while True:
        response = redis_client.get(f"response:{unique_id}")
        if response:
            if isinstance(response, bytes):
                response = response.decode('utf-8')  # Decode if stored as bytes
            redis_client.delete(f"response:{unique_id}")  # Clean up
            break
        await asyncio.sleep(0.5)

    chunked_responses = split_message(response, 2000)
    for response_chunk in chunked_responses:
        await ctx.send(response_chunk)

    do_voice = len(ctx.guild.voice_client.channel.members) - 1 if ctx.guild.voice_client else 0
    print(f"Number of users in voice channel: {do_voice}")

    # Check if the response is long and needs summarizing
    if len(response) > 1000:
        redis_client.lpush('summarizer_queue', json.dumps({"unique_id": unique_id, "message": response}))

        # Poll Redis for the summarizer result
        while True:
            summary_response = redis_client.get(f"summarizer:{unique_id}")
            if summary_response:
                if isinstance(summary_response, bytes):
                    summary_response = summary_response.decode('utf-8')  # Decode if stored as bytes
                redis_client.delete(f"summarizer:{unique_id}")
                await ctx.send(f"{summary_response}")
                # Queue the summarized response for audio generation
                if do_voice:
                    for i in summary_response.split("\n"):
                        redis_client.lpush('audio_queue', f"{unique_id}|{randint(1, 100000)}|{i}")
                break
            await asyncio.sleep(0.5)
    else:
        # Queue the full response for audio generation
        if do_voice:
            for j in response.split("\n"):
                redis_client.lpush('audio_queue', f"{unique_id}|{randint(1, 100000)}|{j}")
