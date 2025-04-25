from discord.ext import commands

import logging

logger = logging.getLogger(__name__)


class ComboBreaker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_message = None

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return  # Ignore bot messages

        content = message.content.strip()

        if self.last_message is None:
            self.last_message = content
            return

        elif content == self.last_message:
            await message.channel.send("🧨 **COMBO BREAKER** 🧨")
            self.last_message = None
        else:
            self.last_message = None


async def setup(bot):
    await bot.add_cog(ComboBreaker(bot))
    logger.info("ComboBreaker Cog loaded successfully.")
