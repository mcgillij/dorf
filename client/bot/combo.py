from discord.ext import commands

import logging

logger = logging.getLogger(__name__)


class ComboBreaker(commands.Cog):
    def __init__(self, bot, combo_threshold=3):
        self.bot = bot
        self.last_message = None
        self.combo_count = 0
        self.combo_threshold = combo_threshold

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return  # Ignore bot messages

        content = message.content.strip()

        if self.last_message is None:
            self.last_message = content
            self.combo_count = 1
            return

        elif content == self.last_message:
            self.combo_count += 1
            if self.combo_count >= self.combo_threshold:
                await message.channel.send("🧨 **COMBO BREAKER** 🧨")
                self.last_message = None
                self.combo_count = 0
        else:
            self.last_message = content
            self.combo_count = 1


async def setup(bot):
    await bot.add_cog(ComboBreaker(bot))
    logger.info("ComboBreaker Cog loaded successfully.")
