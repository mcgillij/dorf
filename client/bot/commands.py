import json
import os
from random import choice

import redis
import discord
from discord.ext import commands
from dotenv import load_dotenv
from voice_manager import connect_to_voice

from bot.utilities import (
    split_message,
    split_text,
    generate_unique_id,
    poll_redis_for_key,
    filtered_responses,
    filter_message,
    logger,
)
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
bot = commands.Bot(command_prefix="!", intents=INTENTS)

LONG_RESPONSE_THRESHOLD = 1000
# Context dictionary
context_dict = {}


# Function to queue message processing
async def queue_message_processing(ctx, message: str):
    unique_id = generate_unique_id(ctx, message)
    logger.info(f"Unique ID: {unique_id}")
    # Store the context if not already stored
    context_dict.setdefault(unique_id, ctx)
    # Queue the message for processing
    redis_client.lpush(
        "response_queue",
        json.dumps({"unique_id": unique_id, "message": f"{ctx.author.id}:{message}"}),
    )
    return unique_id


# Function to process and send responses
async def process_response(ctx, unique_id: str):
    # Poll Redis for the result
    key = f"response:{unique_id}"
    response = await poll_redis_for_key(key)
    # Send the response in chunks
    for response_chunk in split_message(response, 2000):
        await ctx.send(response_chunk)
    # Check for voice channel users
    voice_user_count = (
        len(ctx.guild.voice_client.channel.members) - 1 if ctx.guild.voice_client else 0
    )
    logger.info(f"Number of users in voice channel: {voice_user_count}")
    # Summarize response if it's long
    if len(response) > LONG_RESPONSE_THRESHOLD:
        redis_client.lpush(
            "summarizer_queue",
            json.dumps({"unique_id": unique_id, "message": response}),
        )
        summary_key = f"summarizer:{unique_id}"
        summary_response = await poll_redis_for_key(summary_key)
        await ctx.send(summary_response)
        await process_audio_queue(
            unique_id, [summary_response], voice_user_count
            #unique_id, split_text(summary_response), voice_user_count  # don't have to split here with kokoro (only with mimic)
        )
    else:
        await process_audio_queue(unique_id, [response], voice_user_count)
        #await process_audio_queue(unique_id, split_text(response), voice_user_count)  # no splitting with kokoro


async def process_audio_queue(
    unique_id: str, messages: list[str], voice_user_count: int
):
    """Queues messages for audio generation if users are in the voice channel."""
    if voice_user_count > 0:
        index = 1
        for msg in messages:
            redis_client.lpush("audio_queue", f"{unique_id}|{index}|{msg}")
            index += 1


# Command to handle messages
@bot.command()
async def derf(ctx, *, message: str):
    # Check if the message contains any filtered keywords
    if filter_message(message):
        await ctx.send(choice(filtered_responses))
        return

    unique_id = await queue_message_processing(ctx, message)
    await process_response(ctx, unique_id)

@bot.event
async def on_voice_state_update(member, before, after):
    logger.info(
        f"Voice state update: {member} | Before: {before.channel} | After: {after.channel}"
    )

    if member == bot.user:
        # Handle bot disconnection case
        if not after.channel and before.channel:
            logger.warning("Detected disconnection. Attempting to reconnect...")
            await connect_to_voice()
            return

        # Added logic for when the bot connects/joins a new channel
        elif after.channel and not before.channel:  # Bot just joined this channel
            guild = member.guild  # Guild where the bot is joining
            logger.info(f"Bot has connected to {after.channel} in guild {guild}. Starting capture.")
            await start_capture(guild, after.channel)
            return

    # Existing logic for member joins:
    if after.channel and not before.channel:
        await start_capture(member.guild, after.channel)

    # Handle self mute/deaf changes
    if (before.self_mute != after.self_mute) or (before.self_deaf != after.self_deaf):
        vc = member.guild.voice_client
        if vc and vc.is_listening():
            logger.info(f"Voice state changed for {member}")
            sink = vc.sink
            if isinstance(sink, RingBufferAudioSink):
                await bot.loop.run_in_executor(None, sink.save_user_audio, member.id)


async def start_capture(guild, channel):
    try:
        vc = guild.voice_client
        if not vc or vc.channel != channel:
            vc = await channel.connect(cls=VoiceRecvClient)

        if vc.is_listening():
            logger.info("Already capturing audio.")
            return

        ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        logger.info(f"Recording started in channel {channel}")
    except Exception as e:
        logger.error(f"Error in start_capture: {e}")
