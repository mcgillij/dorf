"""Derfbot, the logical iteration of DORFBOT"""

import os
import asyncio

from bot.commands import bot, nic_bot, connect_to_voice, message_dispatcher
from bot.utilities import setup_logger
from bot.workers.process_response_worker import (
    process_derf_response_queue,
    process_nic_response_queue,
)
from bot.workers.process_summarizer_worker import (
    process_derf_summarizer_queue,
    process_nic_summarizer_queue,
)
from bot.workers.audio_worker import derf_audio_task, nic_audio_task
from bot.workers.playback_worker import playback_derf_task, playback_nic_task
from bot.workers.voice_queue_processor import (
    monitor_derf_response_queue,
    monitor_nic_response_queue,
)

from typing import List, Dict


# Define constants for queue names
DERF_RESPONSE_QUEUE = "voice_response_queue"
DERF_RESPONSE_KEY_PREFIX = "response_queue"
DERF_SUMMARIZER_QUEUE = "summarizer_queue"

NIC_RESPONSE_QUEUE = "voice_nic_response_queue"
NIC_RESPONSE_KEY_PREFIX = "response_nic_queue"
NIC_SUMMARIZER_QUEUE = "summarizer_nic_queue"

logger = setup_logger(__name__)


async def setup_bot(
    bot_instance,
    name: str,
    voice_connector,
    worker_tasks: List[asyncio.Task],
):
    await bot_instance.wait_until_ready()
    logger.info(f"{name} is ready: Logged in as {bot_instance.user.name}")

    await voice_connector(bot_instance)

    for task in worker_tasks:
        asyncio.create_task(task())

    logger.info(f"{name} startup complete")


async def derfbot_ready():
    await setup_bot(
        bot_instance=bot,
        name="DerfBot",
        voice_connector=connect_to_voice,
        worker_tasks=[
            derf_audio_task,
            playback_derf_task,
            process_derf_response_queue,
            process_derf_summarizer_queue,
            message_dispatcher,
            monitor_derf_response_queue,
        ],
    )


async def nicbot_ready():
    await setup_bot(
        bot_instance=nic_bot,
        name="NicBot",
        voice_connector=connect_to_voice,
        worker_tasks=[
            nic_audio_task,
            playback_nic_task,
            process_nic_response_queue,
            process_nic_summarizer_queue,
            monitor_nic_response_queue,
        ],
    )


@bot.event
async def on_ready():
    await derfbot_ready()


@nic_bot.event
async def on_ready():
    await nicbot_ready()


async def main():
    await asyncio.gather(
        bot.start(os.getenv("DISCORD_BOT_TOKEN", "")),
        nic_bot.start(os.getenv("NIC_DISCORD_BOT_TOKEN", "")),
    )


if __name__ == "__main__":
    asyncio.run(main())
