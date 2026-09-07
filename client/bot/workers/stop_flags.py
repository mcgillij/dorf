"""Shared stop/shutup flag checks for the voice workers.

Keys: VOICE_STOP_KEY_PREFIX:<guild_id>:<channel_id>:<all|persona>
"""

import asyncio
import logging

from bot.redis_client import redis_client
from bot.constants import VOICE_STOP_KEY_PREFIX

logger = logging.getLogger(__name__)


async def stop_requested(
    bot_instance, persona: str, guild=None, channel=None
) -> bool:
    """True if a stop/shutup flag is set for this channel/persona.

    Resolves the voice channel from the bot's connected voice client when
    not supplied. Fails open on Redis errors, but logged loudly: dropping
    the item on a transient blip would randomly mute the bot, and a real
    stop is additionally covered by re-checks immediately before play().
    """
    if guild is None or channel is None:
        vc = next(
            (
                v
                for v in bot_instance.voice_clients
                if getattr(v, "channel", None)
            ),
            None,
        )
        if vc is None:
            return False
        guild = vc.channel.guild
        channel = vc.channel

    gid = guild.id
    cid = channel.id
    try:
        for target in ("all", persona):
            key = f"{VOICE_STOP_KEY_PREFIX}:{gid}:{cid}:{target}"
            if await asyncio.to_thread(redis_client.get, key):
                return True
        return False
    except Exception:
        logger.warning(
            "stop_flags.check_failed guild=%s channel=%s persona=%s (fail-open)",
            gid,
            cid,
            persona,
            exc_info=True,
        )
        return False
