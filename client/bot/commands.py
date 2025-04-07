import os
from random import choice
import asyncio
from asyncio import run_coroutine_threadsafe
import dice

import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient

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


@commands.command()
async def check_bots(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("You must be in a voice channel first.")
    bots = [m.name for m in ctx.author.voice.channel.members if m.bot]
    await ctx.send(f"Bots found: {', '.join(bots) if bots else 'No bots detected.'}")


class BaseBot(commands.Bot):
    def __init__(self, name, prefix, *args, **kwargs):
        super().__init__(command_prefix=prefix, intents=INTENTS, *args, **kwargs)
        self.name = name
        self.add_listener(self.on_ready)
        self.add_listener(self.on_voice_state_update)
        self.add_command(check_bots)

    async def on_ready(self):
        logger.info(f"{self.name} is ready.")

    async def on_voice_state_update(self, member, before, after):
        await handle_voice_state_update(self, member, before, after)


@commands.command()
async def search(ctx, *, message: str):
    def callback(param=None):
        if param:
            enqueue_message(ctx.channel, param)

    results = await search_with_tool(message, callback)
    for msg in split_message(results):
        enqueue_message(ctx.channel, msg)


@commands.command()
async def derf(ctx, *, message: str):
    if filter_message(message):
        await ctx.send(choice(filtered_responses))
        return
    uid = await queue_message_processing(ctx, message)
    await process_response(ctx, uid)


class DerfBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="derf_bot", prefix="!", *args, **kwargs)
        self.add_listener(self.on_message)
        self.add_command(search)
        self.add_command(derf)

    async def on_message(self, message):
        # ignore self messages
        if message.author == bot.user:
            return
        if message.content.startswith("/r"):
            roll = message.content.replace("/r", "").replace("oll", "")
            try:
                result = dice.roll(roll)
                result_message = ""
                if len(result) == 1:
                    result_message = f"**{result[0]}**"
                else:
                    result_message = f"{str(result)}: **{sum(result)}**"
                asyncio.run_coroutine_threadsafe(
                    message.channel.send(f":game_die: {roll}: {result_message}"),
                    bot.loop,
                )
            except dice.DiceBaseException as e:
                logger.info(e)
            except dice.DiceFatalError as e:
                logger.info(e)


@commands.command()
async def nic(ctx, *, message: str):
    uid = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, uid)


class NicBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="nic_bot", prefix="#", *args, **kwargs)
        self.add_command(nic)


# shared by the bots
async def handle_voice_state_update(bot, member, before, after):
    logger.info(
        f"{bot.name}: Voice update for {member} | {before.channel} -> {after.channel}"
    )

    if member == bot.user:
        if not after.channel and before.channel:
            logger.warning(f"{bot.name} disconnected. Reconnecting...")
            await connect_to_voice(bot)
        elif after.channel and not before.channel:
            await start_capture(member.guild, after.channel, bot)
    elif after.channel and not before.channel:
        await start_capture(member.guild, after.channel, bot)


async def start_capture(guild, channel, b):
    logger.info(f"in: {b=}")
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
            await voice_channel.connect(cls=VoiceRecvClient)
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
                    await voice_channel.connect(cls=VoiceRecvClient)
            else:
                logger.debug("Already connected to the correct channel.")
    except Exception as e:
        logger.exception(f"Connection error: {str(e)}")


# instantiate my bots here temporarily, need to refactor this
bot = DerfBot()
nic_bot = NicBot()
