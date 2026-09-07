import logging
import time
from random import choice

import asyncio
import discord
from discord.ext import commands

from bot.processing import (
    queue_derf_message_processing,
    queue_nic_message_processing,
    process_derf_response,
    process_nic_response,
)
from bot.utilities import (
    filter_message,
    LLMClient,
    start_capture,
    connect_to_voice,
    voice_capture_watchdog,
    patch_voice_recv_opus_decoder,
)

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
from bot.workers.voice_control_worker import monitor_voice_control_queue

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

EXTENTIONS = [
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
    "bot.statemanager",
]

NIC_EXTENTIONS = ["bot.insulter"]


class BaseBot(commands.Bot):
    def __init__(self, name, prefix, persona, *args, **kwargs):
        super().__init__(command_prefix=prefix, intents=INTENTS, *args, **kwargs)
        self.name = name
        self.persona = persona
        # Optional cog; some workers (e.g., playback) check this attribute.
        self.statemanager = None
        # on_ready can fire again after a full reconnect; workers must only
        # ever be spawned once or competing consumers corrupt the queues.
        self._workers_started = False
        self._worker_tasks = []

    def spawn_workers(self, factories):
        """Start each (name, coroutine_fn) worker once per bot process.

        Workers are supervised: if one dies (an exception outside its internal
        loop guard, or a crash that kills the task), it is restarted with
        exponential backoff so a single failure can't silently kill the voice
        pipeline.

        The once-guard lives in the caller: only call this from a
        `_workers_started`-guarded section whose flag was claimed
        synchronously (before any await), so a second on_ready task can never
        reach it.
        """
        for worker_name, factory in factories:
            task = self.spawn_supervised(worker_name, factory)
            self._worker_tasks.append(task)
        # Capture health watchdog (recovers from voice_recv Opus decode
        # failures). Spawned here so it is covered by the same once-guard as
        # the workers and can never be created twice.
        task = self.spawn_supervised("capture_watchdog", voice_capture_watchdog)
        self._worker_tasks.append(task)

    def spawn_supervised(self, worker_name, factory):
        """Run `factory(self)` forever, restarting it with backoff if it dies."""

        async def _runner():
            delay = 1.0
            while True:
                started = time.monotonic()
                try:
                    await factory(self)
                    # Workers are while-True loops; a normal return means the
                    # loop broke unexpectedly.
                    logger.warning(
                        "%s worker %s exited unexpectedly; restarting.",
                        self.name,
                        worker_name,
                    )
                except asyncio.CancelledError:
                    logger.info(
                        "%s worker %s cancelled; not restarting.",
                        self.name,
                        worker_name,
                    )
                    raise
                except Exception:
                    logger.exception(
                        "%s worker %s crashed; restarting in %.1fs.",
                        self.name,
                        worker_name,
                        delay,
                    )
                # A worker that ran healthy for a while resets the backoff; one
                # that crash-loops backs off up to a minute. Sleep with the
                # current delay first so the first restart is quick.
                ran = time.monotonic() - started
                await asyncio.sleep(delay)
                if ran > 60.0:
                    delay = 1.0
                else:
                    delay = min(delay * 2, 60.0)

        task = asyncio.create_task(_runner(), name=f"{self.name}:{worker_name}")
        return task

    async def on_ready(self):
        logger.info(f"{self.name} is ready.")

    async def handle_voice_state_update(self, member, before, after):
        logger.info(
            f"{self.name}: Voice update for {member} | {before.channel} -> {after.channel}"
        )

        if member == self.user:
            if after.channel and not before.channel:
                logger.info(f"{self.name} joined a voice channel, starting capture.")
                await start_capture(member.guild, after.channel, self)
            elif not after.channel and before.channel:
                suppress_until = (
                    getattr(self, "_suppress_voice_reconnect_until", 0.0) or 0.0
                )
                if suppress_until and time.time() < suppress_until:
                    logger.info(
                        f"{self.name} disconnected as part of intentional reconnect; skipping on_voice_state_update reconnect."
                    )
                    return
                logger.warning(f"{self.name} disconnected. Reconnecting...")
                # connect_to_voice re-attaches the capture sink when it finishes.
                await connect_to_voice(self)
            elif after.channel and before.channel:
                # Bot was moved between channels; re-point capture at the new one.
                logger.info(
                    f"{self.name} moved to {after.channel.name}, restarting capture."
                )
                await start_capture(member.guild, after.channel, self)
            return

        # For regular users:
        if after.channel and not before.channel:
            logger.info(f"User {member} joined a voice channel.")
            if member.guild.voice_client:
                # If bot is already connected, maybe do something
                logger.info(f"Bot already connected, ensuring capture is active.")
                await start_capture(member.guild, after.channel, self)
        elif not after.channel and before.channel:
            # User left voice entirely: drop their capture state (buffer, lock,
            # context) so departed users don't leak memory in the sink.
            sink = getattr(self, "voice_capture_sink", None)
            if sink is not None:
                sink.forget_user(member.id)

    async def on_voice_state_update(self, member, before, after):
        # Ignore other bots, but do process our own voice-state changes so we can
        # restart capture/reconnect when Discord moves/disconnects us.
        if member.bot and member != self.user:
            return
        await self.handle_voice_state_update(member=member, before=before, after=after)


@commands.command()
@commands.cooldown(3, 30.0, commands.BucketType.user)
async def derf(ctx, *, message: str):
    logger.info("in derf")
    if ctx.bot.statemanager:
        await asyncio.to_thread(ctx.bot.statemanager.update_state_thinking)
    if filter_message(message):
        await ctx.send(choice(FILTERED_RESPONSES))
        return
    uid = await queue_derf_message_processing(ctx, message)
    if ctx.bot.statemanager:
        await asyncio.to_thread(ctx.bot.statemanager.update_state_talking)
    await process_derf_response(ctx, uid)
    if ctx.bot.statemanager:
        await asyncio.to_thread(ctx.bot.statemanager.update_state_idle)


@derf.error
async def derf_error(ctx, error):
    # Cooldown hits are user noise, not bugs; everything else re-raises
    # so the normal error handling is untouched.
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Slow down — try again in {error.retry_after:.0f}s")
        return
    raise error


@commands.command()
@commands.cooldown(3, 30.0, commands.BucketType.user)
async def nic(ctx, *, message: str):
    uid = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, uid)


@nic.error
async def nic_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Slow down — try again in {error.retry_after:.0f}s")
        return
    raise error


class NicBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="nic_bot", prefix="#", persona="nic", *args, **kwargs)
        self.add_command(nic)
        self.llm = LLMClient(AUTH_TOKEN, NIC_WORKSPACE, NIC_SESSION_ID)

    async def on_ready(self):
        await super().on_ready()
        patch_voice_recv_opus_decoder()
        logger.info(f"{self.name} is ready. Connecting voice + starting capture...")

        if not self._workers_started:
            # Claim the guard synchronously (before any await) so a second
            # on_ready task can never re-enter this block on a reconnect.
            self._workers_started = True
            self.spawn_workers(
                [
                    ("nic_audio", nic_audio_task),
                    ("nic_playback", playback_nic_task),
                    ("nic_response_worker", process_nic_response_queue),
                    ("nic_summarizer", process_nic_summarizer_queue),
                    ("nic_response_monitor", monitor_nic_response_queue),
                    ("nic_voice_control", monitor_voice_control_queue),
                ]
            )

        await connect_to_voice(self)

        for extension in NIC_EXTENTIONS:
            if extension not in self.extensions:
                try:
                    await self.load_extension(extension)
                except Exception:
                    logger.exception(
                        f"Failed to load extension {extension}; continuing without it."
                    )

        logger.info(f"{self.name} setup complete")


class DerfBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="derfbot", prefix="!", persona="derf", *args, **kwargs)
        logger.info("attaching derf command")
        self.add_command(derf)
        self.llm = LLMClient(AUTH_TOKEN, WORKSPACE, SESSION_ID)

    async def on_ready(self):
        await super().on_ready()
        patch_voice_recv_opus_decoder()
        logger.info(f"{self.name} is ready. Connecting voice + starting capture...")

        if not self._workers_started:
            # Claim the guard synchronously (before any await): discord.py
            # dispatches a new on_ready task per gateway READY, so a second
            # task landing while load_extension below awaits must not
            # re-enter this block and double-load extensions or spawn a
            # competing watchdog.
            self._workers_started = True
            for extension in EXTENTIONS:
                if extension not in self.extensions:
                    try:
                        await self.load_extension(extension)
                    except Exception:
                        logger.exception(
                            f"Failed to load extension {extension}; continuing without it."
                        )

            self.statemanager = self.get_cog("StateManager")
            if not self.statemanager:
                logger.warning("StateManager cog not found!")
            else:
                logger.info("StateManager successfully loaded.")

            self.spawn_workers(
                [
                    ("derf_audio", derf_audio_task),
                    ("derf_playback", playback_derf_task),
                    ("derf_response_worker", process_derf_response_queue),
                    ("derf_summarizer", process_derf_summarizer_queue),
                    ("derf_response_monitor", monitor_derf_response_queue),
                    ("derf_voice_control", monitor_voice_control_queue),
                ]
            )

        await connect_to_voice(self)
        logger.info(f"{self.name} setup complete")
