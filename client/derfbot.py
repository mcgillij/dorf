"""Derfbot, the logical iteration of DORFBOT"""

import os
import asyncio

from bot.commands import bot, nic_bot, connect_to_voice, message_dispatcher
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


async def setup_bot(bot_instance, voice_connector, tasks: list, name: str):
    await bot_instance.wait_until_ready()
    logger.info(f"{name} is ready: Logged in as {bot_instance.user.name}")
    await voice_connector(bot_instance)
    for task in tasks:
        asyncio.create_task(task())
    logger.info(f"{name} startup complete")


@bot.event
async def on_ready():
    await setup_bot(
        bot,
        connect_to_voice,
        [
            mimic_audio_task,
            playback_task,
            process_response_queue,
            process_summarizer_queue,
            monitor_response_queue,
            message_dispatcher,
        ],
        name="DerfBot",
    )


@nic_bot.event
async def on_ready():
    await setup_bot(
        nic_bot,
        connect_to_voice,
        [
            mimic_nic_audio_task,
            playback_nic_task,
            process_nic_response_queue,
            process_nic_summarizer_queue,
            monitor_nic_response_queue,
        ],
        name="NicBot",
    )


async def main():
    await asyncio.gather(
        bot.start(os.getenv("DISCORD_BOT_TOKEN", "")),
        nic_bot.start(os.getenv("NIC_DISCORD_BOT_TOKEN", "")),
    )


if __name__ == "__main__":
    asyncio.run(main())
