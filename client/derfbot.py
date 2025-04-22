"""Derfbot, the logical iteration of DORFBOT"""

import asyncio
import logging
from bot.log_config import setup_logging
from bot.client import derf_bot, nic_bot
from bot.config import NIC_DISCORD_BOT_TOKEN, DISCORD_BOT_TOKEN


setup_logging()
logger = logging.getLogger(__name__)


async def main():
    await asyncio.gather(
        nic_bot.start(NIC_DISCORD_BOT_TOKEN),
        derf_bot.start(DISCORD_BOT_TOKEN),
    )


if __name__ == "__main__":
    asyncio.run(main())
