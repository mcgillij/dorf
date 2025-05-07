import asyncio
import logging
from discord.ext import commands


from bot.utilities import split_message
from bot.lms import (
    search_with_tool,
)

# message queue to not get rate limited hopefully by discord
logger = logging.getLogger(__name__)

message_queue = asyncio.Queue()


# Background task to process the queue
async def message_dispatcher(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        channel, content = await message_queue.get()
        try:
            await channel.send(content)
        except Exception as e:
            print(f"Failed to send message: {e}")
        await asyncio.sleep(1)  # Adjust this to control rate


def enqueue_message(channel, content):
    message_queue.put_nowait((channel, content))


class SearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="search", aliases=["s"])
    async def search(self, ctx, *, message: str):
        """Do a 'deep' search, format: <search query>:str"""
        logger.info("in search")

        def callback(param=None):
            logger.info("in callback")
            if param:
                enqueue_message(ctx.channel, param)

        results = await search_with_tool(message, callback)
        for msg in split_message(results):
            enqueue_message(ctx.channel, msg)


async def setup(bot):
    await bot.add_cog(SearchCog(bot))
    asyncio.create_task(message_dispatcher(bot))
    logger.info("Search Cog loaded successfully.")
