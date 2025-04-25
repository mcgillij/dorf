import os
from random import choice
import logging

import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient

from bot.processing import (
    queue_derf_message_processing,
    queue_nic_message_processing,
    process_derf_response,
    process_nic_response,
)
from bot.utilities import filter_message
from bot.utilities import LLMClient
from bot.audio_capture import RingBufferAudioSink

from bot.constants import (
    WORKSPACE,
    NIC_WORKSPACE,
    SESSION_ID,
    NIC_SESSION_ID,
    FILTERED_RESPONSES,
)
from bot.config import AUTH_TOKEN


logger = logging.getLogger(__name__)


# Configure bot and intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.emojis = True
INTENTS.emojis_and_stickers = True
INTENTS.guilds = True
INTENTS.guild_messages = True
INTENTS.guild_reactions = True
INTENTS.guild_scheduled_events = True
INTENTS.guild_polls = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.moderation = True
INTENTS.polls = True
INTENTS.presences = True
INTENTS.reactions = True
INTENTS.typing = True


class BaseBot(commands.Bot):
    def __init__(self, name, prefix, *args, **kwargs):
        super().__init__(command_prefix=prefix, intents=INTENTS, *args, **kwargs)
        self.name = name
        self.add_listener(self.on_ready)

    async def on_ready(self):
        logger.info(f"{self.name} is ready.")


@commands.command()
async def derf(ctx, *, message: str):
    logger.info("in derf")
    if filter_message(message):
        await ctx.send(choice(FILTERED_RESPONSES))
        return
    uid = await queue_derf_message_processing(ctx, message)
    await process_derf_response(ctx, uid)


@commands.command()
async def nic(ctx, *, message: str):
    uid = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, uid)


class NicBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="nic_bot", prefix="#", *args, **kwargs)
        self.add_command(nic)
        self.llm = LLMClient(AUTH_TOKEN, NIC_WORKSPACE, NIC_SESSION_ID)


class DerfBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="derfbot", prefix="!", *args, **kwargs)
        logger.info("attaching derf command")
        self.add_command(derf)
        self.add_listener(self.on_voice_state_update)
        self.llm = LLMClient(AUTH_TOKEN, WORKSPACE, SESSION_ID)

    async def handle_voice_state_update(self, member, before, after):
        logger.info(
            f"{self.name}: Voice update for {member} | {before.channel} -> {after.channel}"
        )

        if member == self.user:
            if after.channel and not before.channel:
                logger.info(f"{self.name} joined a voice channel, starting capture.")
                await start_capture(member.guild, after.channel, self)
            elif not after.channel and before.channel:
                logger.warning(f"{self.name} disconnected. Reconnecting...")
                await connect_to_voice(self)
            return

        # For regular users:
        if after.channel and not before.channel:
            logger.info(f"User {member} joined a voice channel.")
            if member.guild.voice_client:
                # If bot is already connected, maybe do something
                logger.info(f"Bot already connected, ensuring capture is active.")
                await start_capture(member.guild, after.channel, self)

    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            # Ignore bot events
            return
        await self.handle_voice_state_update(member=member, before=before, after=after)

    async def on_ready(self):
        await super().on_ready()
        logger.info(f"{self.name} is ready. Checking voice connections...")
        for guild in self.guilds:
            voice_channel = discord.utils.get(
                guild.voice_channels, id=int(os.getenv("VOICE_CHANNEL_ID", 0))
            )
            if voice_channel:
                await start_capture(guild, voice_channel, self)


async def start_capture(guild, channel, bot):
    logger.info(f"Starting capture in {channel.name} for bot {bot.name}")
    try:
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            logger.info("Not connected yet. Connecting to voice...")
            vc = await channel.connect(cls=VoiceRecvClient)

        if vc is None or not vc.is_connected():
            logger.error("Failed to connect to voice, aborting capture.")
            return

        if vc.is_listening():
            logger.info("Already listening, resetting sink...")
            vc.stop_listening()

        ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        logger.info(f"Recording started in channel {channel.name}")
        logger.info(f"Sweeping channel {channel.name} for existing members...")
        for member in channel.members:
            if member.bot:
                continue
            logger.info(
                f"Detected existing member {member.display_name}. Initializing capture."
            )
            await bot.handle_voice_state_update(member, None, member.voice)

    except Exception as e:
        logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(bot):
    logger.info(f"BOT STARTING in connect_to_voice: {bot}")
    try:
        guild_id = int(os.getenv("GUILD_ID", ""))
        voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    except ValueError:
        logger.error("GUILD_ID or VOICE_CHANNEL_ID is not a valid integer.")
        return

    if not guild_id or not voice_channel_id:
        logger.error(
            "GUILD_ID or VOICE_CHANNEL_ID is missing in the environment variables."
        )
        return
    guild = discord.utils.get(bot.guilds, id=guild_id)

    if not guild:
        logger.error("Guild not found.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not isinstance(voice_channel, discord.VoiceChannel):
        logger.error(f"Invalid or non-existent voice channel: {voice_channel_id}")
        return

    # Check existing connections in the guild
    current_vc = next((vc for vc in bot.voice_clients if vc.guild == guild), None)

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
