"""Derfbot, the logical iteration of DORFBOT"""

import asyncio
import logging

from bot.bots import DerfBot, NicBot
from bot.log_config import setup_logging
from bot.config import NIC_DISCORD_BOT_TOKEN, DISCORD_BOT_TOKEN
from bot.db import ensure_sqlite_pragmas

setup_logging()
logger = logging.getLogger(__name__)

# WAL + busy_timeout on every database before anything opens them.
ensure_sqlite_pragmas()

# Create bots
derf_bot = DerfBot()
nic_bot = NicBot()


async def main():
    try:
        await asyncio.gather(
            nic_bot.start(NIC_DISCORD_BOT_TOKEN),
            derf_bot.start(DISCORD_BOT_TOKEN),
        )
    finally:
        # One bot failing (bad token, network) used to abandon the other
        # mid-connection with no cleanup.
        await asyncio.gather(
            nic_bot.close(), derf_bot.close(), return_exceptions=True
        )


if __name__ == "__main__":
    asyncio.run(main())
