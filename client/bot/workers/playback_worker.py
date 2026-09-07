import asyncio
import os
import time
import discord
import logging
from bot.constants import DERF_PLAYBACK_QUEUE, NIC_PLAYBACK_QUEUE
from bot.redis_client import redis_client
from bot.config import VOICE_CHANNEL_ID
from bot.utilities import connect_to_voice
from bot.workers.stop_flags import stop_requested

logger = logging.getLogger(__name__)


async def _stop_requested(bot_instance, queue_name, guild, channel) -> bool:
    persona = "nic" if queue_name == NIC_PLAYBACK_QUEUE else "derf"
    return await stop_requested(bot_instance, persona, guild=guild, channel=channel)


async def _play_item(
    bot_instance, queue_name, voice_channel_id, unique_id, opus_path
) -> bool:
    """Play a single queued item. Returns True if playback started+finished."""
    # Fetch the voice channel by ID
    channel = bot_instance.get_channel(voice_channel_id)
    if not channel or not isinstance(channel, discord.VoiceChannel):
        logger.info(f"Voice channel {voice_channel_id} not found or invalid.")
        return False

    # Get the voice client for the guild
    guild = channel.guild
    voice_client = discord.utils.get(bot_instance.voice_clients, guild=guild)

    if not voice_client or not voice_client.is_connected():
        logger.info("Voice client not connected. Attempting to reconnect...")
        try:
            await connect_to_voice(bot_instance)
            voice_client = discord.utils.get(
                bot_instance.voice_clients, guild=guild
            )
        except discord.ClientException as e:
            logger.error(f"Error connecting to voice channel: {e}")
            return False

    if not voice_client or not voice_client.is_connected():
        logger.error("playback_worker.reconnect_failed unique_id=%s", unique_id)
        return False

    # Check to see if there's any humans in the channel
    has_humans = any(not member.bot for member in channel.members)
    if not has_humans:
        logger.info(
            f"Skipping playback as there are only bots in {channel.name}."
        )
        return False

    # If someone just said stop/shutup, skip playback.
    if await _stop_requested(bot_instance, queue_name, guild, channel):
        logger.info(
            "playback_worker.skipped_due_to_stop unique_id=%s queue=%s",
            unique_id,
            queue_name,
        )
        return False

    # state for godot bot (sync sqlite — keep it off the event loop)
    if bot_instance.statemanager:
        await asyncio.to_thread(bot_instance.statemanager.update_state_talking)

    played = False
    try:
        # Play the generated audio. If a leftover player is still active
        # (play() would raise 'Already playing audio' and we'd drop this
        # item), give it a bounded grace period to finish first, then
        # drop the item explicitly instead of letting play() throw.
        grace_started = time.monotonic()
        while (
            voice_client.is_playing() and time.monotonic() - grace_started < 30.0
        ):
            await asyncio.sleep(0.1)
        if voice_client.is_playing():
            logger.error(
                "playback_worker.leftover_player_still_playing unique_id=%s "
                "(dropping item)",
                unique_id,
            )
            return False

        # from_probe is an async classmethod; it must be awaited, not run
        # via to_thread (which would return an un-awaited coroutine).
        audio_source = await discord.FFmpegOpusAudio.from_probe(
            opus_path, method="fallback", options="-vn -b:a 128k"
        )

        # Final stop re-check: a stop issued during the grace/probe window
        # must still suppress this item before it starts.
        if await _stop_requested(bot_instance, queue_name, guild, channel):
            logger.info(
                "playback_worker.skipped_due_to_stop_late unique_id=%s", unique_id
            )
            return False

        # Event-driven completion: the after= callback runs on the audio
        # player thread, so it must wake the loop thread-safely
        # (call_soon alone does not wake the selector).
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def _after(error):
            if error:
                logger.error(
                    "playback.player_error unique_id=%s error=%s", unique_id, error
                )
            loop.call_soon_threadsafe(done.set)

        voice_client.play(audio_source, after=_after)
        played = True

        timeout_s = float(os.getenv("PLAYBACK_TIMEOUT_S", "300"))
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.error(
                "playback.timeout unique_id=%s timeout_s=%s", unique_id, timeout_s
            )
            # stop_playing() only halts audio; voice_client.stop() on a
            # VoiceRecvClient also tears down listening (capture deafness).
            stop_fn = getattr(voice_client, "stop_playing", voice_client.stop)
            stop_fn()
    finally:
        # state for godot bot — always reset, even on failure paths
        if bot_instance.statemanager:
            await asyncio.to_thread(bot_instance.statemanager.update_state_idle)
    return played


async def playback_task(bot_instance, queue_name, voice_channel_id):
    """
    Base function to process playback requests from a Redis queue.
    """
    while True:
        try:
            playback_data = await asyncio.to_thread(redis_client.rpop, queue_name)
            if not playback_data:
                await asyncio.sleep(1)
                continue

            # Parse the playback data
            unique_id, opus_path = playback_data.split("|", 1)
            try:
                ok = await _play_item(
                    bot_instance, queue_name, voice_channel_id, unique_id, opus_path
                )
                if not ok:
                    logger.info(
                        "playback_worker.item_dropped unique_id=%s opus_path=%s",
                        unique_id,
                        opus_path,
                    )
            finally:
                # The opus file is removed on every path (played, skipped,
                # or errored) so nothing leaks in /tmp or output/.
                if os.path.exists(opus_path):
                    try:
                        os.remove(opus_path)
                    except OSError:
                        logger.warning(
                            "playback_worker.opus_cleanup_failed opus_path=%s",
                            opus_path,
                            exc_info=True,
                        )

        except Exception as e:
            logger.exception(f"Error in playback_task_base: {e}")
            await asyncio.sleep(1)  # Avoid spamming on continuous errors


async def playback_derf_task(bot):
    """
    Wrapper for the playback task for the main bot.
    """
    await playback_task(bot, DERF_PLAYBACK_QUEUE, VOICE_CHANNEL_ID)


async def playback_nic_task(bot):
    """
    Wrapper for the playback task for the nic_bot.
    """
    await playback_task(bot, NIC_PLAYBACK_QUEUE, VOICE_CHANNEL_ID)
