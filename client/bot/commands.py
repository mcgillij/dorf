import os
from random import choice
import asyncio
from asyncio import run_coroutine_threadsafe
import dice

import discord
from discord.ext import commands, voice_recv
from dotenv import load_dotenv

from bot.processing import (
    queue_message_processing,
    queue_nic_message_processing,
    process_response,
    process_nic_response,
)

from bot.utilities import filtered_responses, filter_message, split_message

from bot.log_config import setup_logger

from bot.lms import (
    search_with_tool,
)

from bot.audio_capture import RingBufferAudioSink

load_dotenv()

logger = setup_logger(__name__)

# Configure bot and intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.members = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)
nic_bot = commands.Bot(command_prefix="#", intents=INTENTS)

# message queue to not get rate limited hopefully by discord
message_queue = asyncio.Queue()


# Background task to process the queue
async def message_dispatcher():
    await bot.wait_until_ready()
    while not bot.is_closed():
        channel, content = await message_queue.get()
        try:
            await channel.send(content)
        except Exception as e:
            print(f"Failed to send message: {e}")
        await asyncio.sleep(1)  # Adjust this to control rate


# Enqueue a message
def enqueue_message(channel, content):
    message_queue.put_nowait((channel, content))

@bot.event
async def on_message(message):
    # ignore self messages
    if message.author == bot.user:
        return
    if message.content.startswith('/r'):
        roll = message.content.replace('/r', '')
        roll = roll.replace('oll ', '')
        try:
            result = dice.roll(roll)
            result_message = ''
            if len(result) == 1:
                result_message = f'**{result[0]}**'
            else:
                result_message = f'{str(result)}: **{sum(result)}**'
            asyncio.run_coroutine_threadsafe(message.channel.send(f':game_die: {roll}: {result_message}'), bot.loop)
        except dice.DiceBaseException as e:
            logger.info(e)
        except dice.DiceFatalError as e:
            logger.info(e)

# Command to handle messages
@bot.command()
async def search(ctx, *, message: str):

    loop = asyncio.get_event_loop()  # Or pass it in beforehand to be safe

    def progress_update_callback(param=None):
        if param:
            logger.info(
                f"######### IN THE CALLBACK ###################### Progress update: {param}"
            )
            enqueue_message(ctx.channel, f"{param}")
            # run_coroutine_threadsafe(ctx.send(f"{param}"), loop)
            # asyncio.create_task(ctx.send(f"{param}"))
            # await ctx.send(f"{param}")
        else:
            logger.info("############ In callback but param empty ########")

    results = await search_with_tool(message, progress_update_callback)
    messages = split_message(results)
    for m in messages:
        enqueue_message(ctx.channel, m)


@bot.command()
async def derf(ctx, *, message: str):
    # Check if the message contains any filtered keywords
    if filter_message(message):
        await ctx.send(choice(filtered_responses))
        return

    unique_id = await queue_message_processing(ctx, message)
    await process_response(ctx, unique_id)


@nic_bot.command()
async def nic(ctx, *, message: str):
    unique_id = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, unique_id)


@bot.command()
async def check_bots(ctx):
    # Ensure the user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("You must be in a voice channel first.")

    channel = ctx.author.voice.channel
    bots = [member for member in channel.members if member.bot]

    if bots:
        bot_names = ", ".join([m.name for m in bots])
        await ctx.send(f"Bots found: {bot_names}")
    else:
        await ctx.send("No bots detected here.")


@nic_bot.event
@bot.event
async def on_voice_state_update(member, before, after):
    for b in [bot, nic_bot]:
        logger.info(
            f"Voice state update: {member} | Before: {before.channel} | After: {after.channel}"
        )

        if member == b.user:
            # Handle bot disconnection case
            if not after.channel and before.channel:
                logger.warning("Detected disconnection. Attempting to reconnect...")
                await connect_to_voice(b)
                return

            # Added logic for when the bot connects/joins a new channel
            elif after.channel and not before.channel:  # Bot just joined this channel
                guild = member.guild  # Guild where the bot is joining
                logger.info(
                    f"Bot has connected to {after.channel} in guild {guild}. Starting capture."
                )
                await start_capture(guild, after.channel)
                return

        # Existing logic for member joins:
        if after.channel and not before.channel:
            await start_capture(member.guild, after.channel)

        # Handle self mute/deaf changes
        if (before.self_mute != after.self_mute) or (
            before.self_deaf != after.self_deaf
        ):
            vc = member.guild.voice_client
            if vc and vc.is_listening():
                logger.info(f"Voice state changed for {member}")
                sink = vc.sink
                if isinstance(sink, RingBufferAudioSink):
                    await b.loop.run_in_executor(None, sink.save_user_audio, member.id)


async def start_capture(guild, channel):
    for b in [bot, nic_bot]:
        try:
            vc = guild.voice_client
            if not vc or vc.channel != channel:
                vc = await channel.connect(cls=VoiceRecvClient)

            if vc.is_listening():
                logger.info("Already capturing audio.")
                return

            ring_buffer_sink = RingBufferAudioSink(bot=b, buffer_size=1024 * 1024)
            vc.listen(ring_buffer_sink)
            logger.info(f"Recording started in channel {channel}")
        except Exception as e:
            logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(b):
    guild_id = int(os.getenv("GUILD_ID", ""))
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    guild = discord.utils.get(b.guilds, id=guild_id)

    if not guild:
        logger.error("Guild not found.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not isinstance(voice_channel, discord.VoiceChannel):
        logger.error(f"Invalid or non-existent voice channel: {voice_channel_id}")
        return

    # Check existing connections in the guild
    current_vc = next((vc for vc in b.voice_clients if vc.guild == guild), None)

    try:
        if not current_vc:
            await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
            logger.info(f"Connected to {voice_channel.name}")
        else:
            # Check if already connected to the correct channel
            if current_vc.channel.id != voice_channel.id:
                # Move existing client or reconnect?
                try:
                    await current_vc.move_to(voice_channel)  # Attempt move first
                    logger.info(f"Moved to {voice_channel.name}")
                except discord.errors.InvalidData as e:
                    logger.error(f"Move failed: {e}. Reconnecting...")
                    await current_vc.disconnect()
                    await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
            else:
                logger.debug("Already connected to the correct channel.")
    except Exception as e:
        logger.exception(f"Connection error: {str(e)}")
