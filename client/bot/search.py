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

# The bot's running loop, captured at setup, so executor-thread producers
# can enqueue via call_soon_threadsafe instead of a cross-thread put_nowait.
_BOT_LOOP = None


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
    item = (channel, content)
    if _BOT_LOOP is not None:
        # Producers may run on an executor thread's private event loop, where
        # put_nowait on the bot's queue is unsafe; marshal to the bot loop.
        _BOT_LOOP.call_soon_threadsafe(message_queue.put_nowait, item)
    else:
        message_queue.put_nowait(item)


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

        try:
            results = await search_with_tool(message, callback)
        except Exception:
            logger.exception("Search command failed")
            await ctx.send("Search failed — LM Studio may be down.")
            return
        for msg in split_message(results):
            enqueue_message(ctx.channel, msg)


async def setup(bot):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()

    cog = SearchCog(bot)
    await bot.add_cog(cog)
    # Keep a reference so the dispatcher task isn't garbage-collected.
    cog.dispatcher_task = asyncio.create_task(message_dispatcher(bot))
    logger.info("Search Cog loaded successfully.")
