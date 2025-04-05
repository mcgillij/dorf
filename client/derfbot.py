"""Derfbot, the logical iteration of DORFBOT"""

import os
import asyncio

from bot.commands import bot, nic_bot, connect_to_voice
from bot.utilities import setup_logger
from bot.workers.process_response_worker import (
    process_response_queue,
    process_nic_response_queue,
)
from bot.workers.process_summarizer_worker import (
    process_summarizer_queue,
    process_nic_summarizer_queue,
)
from bot.workers.mimic_audio_worker import mimic_audio_task, mimic_nic_audio_task
from bot.workers.playback_worker import playback_task, playback_nic_task
from bot.workers.voice_queue_processor import (
    monitor_response_queue,
    monitor_nic_response_queue,
)

logger = setup_logger(__name__)


@bot.event
@nic_bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}")
    await bot.wait_until_ready()
    await connect_to_voice(bot)
    asyncio.create_task(mimic_audio_task())
    asyncio.create_task(playback_task())
    asyncio.create_task(process_response_queue())
    asyncio.create_task(process_summarizer_queue())
    asyncio.create_task(monitor_response_queue())
    logger.info("done kicking off the bot")

    logger.info(f"Logged in as {nic_bot.user.name}")
    await nic_bot.wait_until_ready()
    await connect_to_voice(nic_bot)
    asyncio.create_task(mimic_nic_audio_task())
    asyncio.create_task(playback_nic_task())
    asyncio.create_task(process_nic_response_queue())
    asyncio.create_task(process_nic_summarizer_queue())
    asyncio.create_task(monitor_nic_response_queue())
    logger.info("done kicking off the nic_bot")


async def main():
    await asyncio.gather(
        bot.start(os.getenv("DISCORD_BOT_TOKEN", "")),
        nic_bot.start(os.getenv("NIC_DISCORD_BOT_TOKEN", "")),
    )


if __name__ == "__main__":
    asyncio.run(main())
