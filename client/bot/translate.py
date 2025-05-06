import logging

from discord.ext import commands
from bot.lms import translate_to_indian, translate_to_english

logger = logging.getLogger(__name__)


class Translate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="translate_indian", aliases=["tr", "archa"])
    async def translate_to_english(self, ctx, *, text: str):
        """Translates from Marathi to English"""
        translated_text = await translate_to_english(text)
        # send the message back to the channel
        await ctx.send(f"{translated_text}")

    @commands.command(name="translate_english", aliases=["tra", "rarcha"])
    async def translate_to_indian(self, ctx, *, text: str):
        """Translates from English to Marathi"""
        translated_text = await translate_to_indian(text)
        # send the message back to the channel
        await ctx.send(f"{translated_text}")


async def setup(bot):
    await bot.add_cog(Translate(bot))
    logger.info("Translate cog loaded.")
