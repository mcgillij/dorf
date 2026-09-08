"""Derfbot, the logical iteration of DORFBOT"""

import asyncio
import logging
import signal

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
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    # systemd sends SIGTERM on `systemctl stop`; ./kill sends SIGTERM/INT.
    # Handle both instead of dying mid-write with no cleanup.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("derfbot starting: logging in both bots.")
    run = asyncio.gather(
        nic_bot.start(NIC_DISCORD_BOT_TOKEN),
        derf_bot.start(DISCORD_BOT_TOKEN),
    )
    run_task = asyncio.ensure_future(run)
    stop_task = asyncio.ensure_future(stop.wait())

    try:
        await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        if not run_task.done():
            logger.info("shutdown signal received; stopping workers and bots.")
            await asyncio.gather(
                nic_bot.shutdown(), derf_bot.shutdown(), return_exceptions=True
            )
        # Reap the gather either way; a bot that failed to start must NOT
        # vanish silently (the old version swallowed login failures here).
        results = await asyncio.gather(run_task, return_exceptions=True)
        for exc in results:
            if isinstance(exc, BaseException) and not isinstance(exc, asyncio.CancelledError):
                logger.error("bot start failed", exc_info=exc)


if __name__ == "__main__":
    asyncio.run(main())
