import json
import hashlib
import asyncio
import os
from random import choice

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

filtered_responses = [
    "Nice try nerd!",
    "Nice try, but you're still a noob.",
    "Almost there, but not close enough.",
    "You missed by a mile!",
    "Not quite, keep trying harder!",
    "Close, but you need to step it up.",
    "You were almost there, but not quite.",
    "Good effort, now go learn more.",
    "Almost had it, but not quite enough.",
    "Nice shot, just a little off.",
    "Almost got it, but still missing the mark.",
    "You're close, but need to practice more.",
    "Almost there, but you’re slipping.",
    "Good try, now go study up.",
    "Not exactly right, but keep trying!",
    "Close enough for a joke, but not real.",
    "Nice effort, just need a bit more focus.",
    "You were almost there, but still off.",
    "Good start, now go get it right.",
    "Almost had it, but missed by a long shot.",
    "Nice attempt, but you're not there yet.",
    "Close, but you need to work harder.",
    "You were almost there, but still off-base.",
    "Good effort, now go get it right.",
    "Almost got it, just a little more.",
    "Nice try, but the answer eludes you.",
    "Close enough for a laugh, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost had it, just need to focus more.",
    "Nice try, but the answer is eluding you.",
    "Close enough for a joke, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost got it, just need to focus more.",
    "Nice attempt, but the answer is elusive.",
    "Close enough for a laugh, not real.",
    "You were almost there, but still off.",
    "Good effort, now go get it right!",
    "Almost had it, just need to focus more.",
    "Nice try, but the answer is elusive."
    ]

@bot.command()
async def derf(ctx, *, message: str):
    filtered_keywords = {"behavior driven development", "QA", "BDD", "pytest", "testing", "gherkin", "test", "specflow", "cypress", "playwrite"}  # Add the keywords to filter

    # Check if the message contains any filtered keywords
    if any(keyword.lower() in message.lower() for keyword in filtered_keywords):
        await ctx.send(choice(filtered_responses))
        #await ctx.send("Nice try nerd!")
        return

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
    if before.self_mute != after.self_mute or before.self_deaf != after.self_deaf:
        # Voice state changed (like PTT release)
        vc = member.guild.voice_client
        if vc and vc.is_listening():
            print(f"Voice state changed for {member}")
            sink = vc.sink
            if isinstance(sink, RingBufferAudioSink):
                print(f"Saving audio due to voice state change for {member}")
                await bot.loop.run_in_executor(None, sink.save_user_audio, member.id)

async def start_capture(guild, channel):
    try:
        vc = guild.voice_client
        if not vc or vc.channel != channel:
            vc = await channel.connect(cls=VoiceRecvClient)
        
        if vc.is_listening():
            print("Already capturing audio.")
            return
            
        ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        print(f"Recording started in channel {channel}")
    except Exception as e:
        print(f"Error in start_capture: {e}")
