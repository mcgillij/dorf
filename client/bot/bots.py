import os
from random import choice
import logging

import asyncio
import discord
from discord.ext import commands

from bot.processing import (
    queue_derf_message_processing,
    queue_nic_message_processing,
    process_derf_response,
    process_nic_response,
)
from bot.utilities import filter_message, LLMClient, start_capture, connect_to_voice

from bot.workers.process_response_worker import (
    process_derf_response_queue,
    process_nic_response_queue,
)
from bot.workers.process_summarizer_worker import (
    process_derf_summarizer_queue,
    process_nic_summarizer_queue,
)
from bot.workers.audio_worker import derf_audio_task, nic_audio_task
from bot.workers.playback_worker import playback_derf_task, playback_nic_task
from bot.workers.voice_queue_processor import (
    monitor_derf_response_queue,
    monitor_nic_response_queue,
)

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
INTENTS.dm_messages = True


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

    async def on_ready(self):
        await connect_to_voice(self)
        worker_tasks = [
            lambda: nic_audio_task(self),
            lambda: playback_nic_task(self),
            lambda: process_nic_response_queue(self),
            lambda: process_nic_summarizer_queue(self),
            lambda: monitor_nic_response_queue(self),
        ]
        for task in worker_tasks:
            asyncio.create_task(task())
        logger.info(f"{self.name} setup complete")


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

        extensions = [
            "bot.leveling",
            "bot.adventure",
            "bot.quotes",
            "bot.emoji",
            "bot.poll",
            "bot.misc",
            "bot.search",
            "bot.macro",
            "bot.faction",
            "bot.combo",
            "bot.metrics",
            "bot.sdcog",
            "bot.news",
            "bot.translate",
        ]
        for extension in extensions:
            if extension not in self.extensions:
                await self.load_extension(extension)

        await connect_to_voice(self)
        worker_tasks = [
            lambda: derf_audio_task(self),
            lambda: playback_derf_task(self),
            lambda: process_derf_response_queue(self),
            lambda: process_derf_summarizer_queue(self),
            lambda: monitor_derf_response_queue(self),
        ]
        for task in worker_tasks:
            asyncio.create_task(task())
        logger.info(f"{self.name} setup complete")
