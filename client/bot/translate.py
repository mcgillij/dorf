import logging

from discord.ext import commands
from bot.lms import translate

logger = logging.getLogger(__name__)


class Translate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="translate", aliases=["tr", "archa"])
    async def translate(self, ctx, *, text: str):
        translated_text = await translate(text)
        # send the message back to the channel
        await ctx.send(f"{translated_text}")


async def setup(bot):
    await bot.add_cog(Translate(bot))
    logger.info("Translate cog loaded.")
