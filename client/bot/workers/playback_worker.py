import asyncio
import os
import time
import discord
import logging
from bot.constants import DERF_PLAYBACK_QUEUE, NIC_PLAYBACK_QUEUE
from bot.redis_client import redis_client
from bot.config import VOICE_CHANNEL_ID
from bot.utilities import connect_to_voice
from bot.constants import VOICE_STOP_KEY_PREFIX

logger = logging.getLogger(__name__)


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

            # Fetch the voice channel by ID
            channel = bot_instance.get_channel(voice_channel_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                logger.info(f"Voice channel {voice_channel_id} not found or invalid.")
                continue

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
                    continue

            # Check to see if there's any humans in the channel
            has_humans = any(not member.bot for member in channel.members)

            if not has_humans:
                logger.info(
                    f"Skipping playback as there are only bots in {channel.name}."
                )
                continue  # Skip to the next iteration

            # If someone just said stop/shutup, skip playback.
            try:
                gid = guild.id
                cid = channel.id
                persona = "nic" if queue_name == NIC_PLAYBACK_QUEUE else "derf"
                stop_keys = [
                    f"{VOICE_STOP_KEY_PREFIX}:{gid}:{cid}:all",
                    f"{VOICE_STOP_KEY_PREFIX}:{gid}:{cid}:{persona}",
                ]
                stopped = False
                for k in stop_keys:
                    if await asyncio.to_thread(redis_client.get, k):
                        stopped = True
                        break
                if stopped:
                    logger.info(
                        "playback_worker.skipped_due_to_stop unique_id=%s persona=%s opus_path=%s",
                        unique_id,
                        persona,
                        opus_path,
                    )
                    if os.path.exists(opus_path):
                        os.remove(opus_path)
                    continue
            except Exception:
                pass
            # state for godot bot (sync sqlite — keep it off the event loop)
            if bot_instance.statemanager:
                await asyncio.to_thread(bot_instance.statemanager.update_state_talking)

            # Play the generated audio. If a leftover player is still active
            # (play() would raise 'Already playing audio' and we'd drop this
            # item), give it a bounded grace period to finish first.
            grace_started = time.monotonic()
            while voice_client.is_playing() and time.monotonic() - grace_started < 30.0:
                await asyncio.sleep(0.1)

            # from_probe is an async classmethod; it must be awaited, not run
            # via to_thread (which would return an un-awaited coroutine).
            audio_source = await discord.FFmpegOpusAudio.from_probe(
                opus_path, method="fallback", options="-vn -b:a 128k"
            )

            # Event-driven completion: the after= callback sets an event rather
            # than busy-polling is_playing(), and the wait is bounded so a hung
            # player (mid-play disconnect stalls up to 30s) can't wedge the
            # worker forever.
            done = asyncio.Event()

            def _after(error):
                if error:
                    logger.error(
                        "playback.player_error unique_id=%s error=%s", unique_id, error
                    )
                done.set()

            voice_client.play(audio_source, after=_after)

            timeout_s = float(os.getenv("PLAYBACK_TIMEOUT_S", "300"))
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.error(
                    "playback.timeout unique_id=%s timeout_s=%s", unique_id, timeout_s
                )
                voice_client.stop()
            # state for godot bot
            if bot_instance.statemanager:
                await asyncio.to_thread(bot_instance.statemanager.update_state_idle)
            # Clean up the opus file
            if os.path.exists(opus_path):
                os.remove(opus_path)

        except Exception as e:
            logger.error(f"Error in playback_task_base: {e}")
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
