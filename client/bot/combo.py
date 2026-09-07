from discord.ext import commands

import logging
import time
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# Prune stale per-channel combo state once we're tracking this many channels.
COMBO_PRUNE_THRESHOLD = 200
COMBO_STALE_SECONDS = 3600  # 1 hour


class ComboBreaker(commands.Cog):
    def __init__(self, bot, combo_threshold=3):
        self.bot = bot
        # Per (guild_id, channel_id) state so a combo is only tracked/broken
        # by messages in the same channel, not anywhere the bot can see.
        self.combos: Dict[Tuple[int, int], dict] = {}
        self.combo_threshold = combo_threshold

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return  # Ignore bot messages

        if message.guild is None:
            return  # DMs have no (guild, channel) key; skip combo tracking

        # Light cleanup: drop stale channel state to bound memory usage.
        if len(self.combos) > COMBO_PRUNE_THRESHOLD:
            now = time.monotonic()
            self.combos = {
                k: v
                for k, v in self.combos.items()
                if now - v["last_seen"] < COMBO_STALE_SECONDS
            }

        key = (message.guild.id, message.channel.id)
        state = self.combos.get(key)

        content = message.content.strip()

        if state is None or state["last_message"] is None:
            self.combos[key] = {
                "last_message": content,
                "combo_count": 1,
                "last_seen": time.monotonic(),
            }
            return

        elif content == state["last_message"]:
            state["combo_count"] += 1
            if state["combo_count"] >= self.combo_threshold:
                await message.channel.send("🧨 **COMBO BREAKER** 🧨")
                state["last_message"] = None
                state["combo_count"] = 0
        else:
            state["last_message"] = content
            state["combo_count"] = 1

        state["last_seen"] = time.monotonic()


async def setup(bot):
    await bot.add_cog(ComboBreaker(bot))
    logger.info("ComboBreaker Cog loaded successfully.")
