import json
import asyncio
import logging

import discord

from bot.redis_client import redis_client
from bot.constants import (
    VOICE_STOP_KEY_PREFIX,
    DERF_AUDIO_QUEUE,
    NIC_AUDIO_QUEUE,
    DERF_PLAYBACK_QUEUE,
    NIC_PLAYBACK_QUEUE,
    VOICE_CONTROL_DERF_QUEUE,
    VOICE_CONTROL_NIC_QUEUE,
)

logger = logging.getLogger(__name__)

# Each bot consumes only its own control queue so a stop for one persona can
# never be swallowed by the other bot's worker.
CONTROL_QUEUES = {
    "derf": VOICE_CONTROL_DERF_QUEUE,
    "nic": VOICE_CONTROL_NIC_QUEUE,
}


def _stop_key(guild_id: int | None, channel_id: int | None, target: str) -> str:
    # Keep keys stable even if metadata is missing.
    gid = str(guild_id or 0)
    cid = str(channel_id or 0)
    return f"{VOICE_STOP_KEY_PREFIX}:{gid}:{cid}:{target}"


async def _set_stop_flags(
    *, guild_id: int | None, channel_id: int | None, target: str, ttl_s: int
) -> None:
    # Always set "all" when stopping a specific persona, so shared workers can obey it.
    keys = {_stop_key(guild_id, channel_id, "all")}
    keys.add(_stop_key(guild_id, channel_id, target))
    for key in keys:
        try:
            await asyncio.to_thread(redis_client.set, key, "1", ex=ttl_s)
        except Exception:
            pass


async def _clear_queues(target: str) -> None:
    # Best-effort clears; safe even if queues are empty.
    queues: list[str] = []
    if target in ("derf", "all"):
        queues.extend([DERF_AUDIO_QUEUE, DERF_PLAYBACK_QUEUE])
    if target in ("nic", "all"):
        queues.extend([NIC_AUDIO_QUEUE, NIC_PLAYBACK_QUEUE])

    for q in queues:
        try:
            await asyncio.to_thread(redis_client.delete, q)
        except Exception:
            pass


async def _stop_voice_client(
    bot_instance, *, guild_id: int | None, channel_id: int | None
) -> None:
    # Try to find the right voice client.
    voice_client = None
    try:
        if guild_id:
            guild = discord.utils.get(bot_instance.guilds, id=int(guild_id))
            if guild:
                voice_client = discord.utils.get(
                    bot_instance.voice_clients, guild=guild
                )
        if voice_client is None and channel_id:
            channel = bot_instance.get_channel(int(channel_id))
            if channel is not None:
                guild = channel.guild
                voice_client = discord.utils.get(
                    bot_instance.voice_clients, guild=guild
                )
        if voice_client is None and bot_instance.voice_clients:
            voice_client = bot_instance.voice_clients[0]
    except Exception:
        voice_client = None

    if voice_client and voice_client.is_connected():
        try:
            if voice_client.is_playing():
                voice_client.stop()
        except Exception:
            pass


async def monitor_voice_control_queue(bot_instance, *, stop_ttl_s: int = 10) -> None:
    """Consumes this bot's voice control queue and applies stop/shutup actions.

    This runs per-bot process, so it can directly stop the local voice client.
    """

    persona = getattr(bot_instance, "persona", None) or "derf"
    control_queue = CONTROL_QUEUES.get(persona, VOICE_CONTROL_DERF_QUEUE)
    logger.info(
        "voice_control.start queue=%s stop_ttl_s=%s bot=%s",
        control_queue,
        stop_ttl_s,
        getattr(bot_instance, "name", None),
    )
    while True:
        try:
            result = await asyncio.to_thread(redis_client.blpop, control_queue, 30)
            if not result or len(result) < 2:
                continue
            _, raw = result
            data = json.loads(raw)

            action = (data.get("action") or "").lower()
            if action != "stop":
                continue

            target = (data.get("target") or persona).lower()
            if target not in ("all", "derf", "nic"):
                target = "all"

            guild_id = data.get("guild_id")
            channel_id = data.get("channel_id")
            trace_id = data.get("trace_id")

            # Resolve stop keys from this bot's actual voice channel when we can,
            # so they always match the keys checked by the audio/playback workers
            # even if capture metadata was missing.
            vc = next(
                (v for v in bot_instance.voice_clients if getattr(v, "channel", None)),
                None,
            )
            if vc is not None:
                resolved_guild_id = vc.channel.guild.id
                resolved_channel_id = vc.channel.id
            else:
                resolved_guild_id = guild_id or 0
                resolved_channel_id = channel_id or 0

            logger.info(
                "voice_control.stop trace_id=%s target=%s guild_id=%s channel_id=%s",
                trace_id,
                target,
                resolved_guild_id,
                resolved_channel_id,
            )

            # Stop any current playback immediately.
            await _stop_voice_client(
                bot_instance, guild_id=resolved_guild_id, channel_id=resolved_channel_id
            )

            # Suppress new playback briefly and clear pending queues.
            await _set_stop_flags(
                guild_id=resolved_guild_id,
                channel_id=resolved_channel_id,
                target=target,
                ttl_s=stop_ttl_s,
            )
            await _clear_queues(target)

        except Exception as e:
            logger.exception("voice_control.loop_error err=%s", e)
            await asyncio.sleep(1)
