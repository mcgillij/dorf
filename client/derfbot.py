"""Derfbot, the logical iteration of DORFBOT"""

import os
import asyncio

from bot.commands import bot, nic_bot, connect_to_voice, message_dispatcher
from bot.utilities import setup_logger, derf_bot, nicole_bot
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
from bot.workers.voice_queue_processor import start_monitor_task
from bot.processing import process_derf, process_nic
from typing import List, Dict


logger = setup_logger(__name__)


async def setup_bot(
    bot_instance,
    name: str,
    voice_connector,
    worker_tasks: List[asyncio.Task],
    monitor_config: Dict,
):
    await bot_instance.wait_until_ready()
    logger.info(f"{name} is ready: Logged in as {bot_instance.user.name}")

    await voice_connector(bot_instance)

    for task in worker_tasks:
        asyncio.create_task(task())

    start_monitor_task(
        bot_instance=bot_instance,
        bot_logic=monitor_config["bot_logic"],
        queue_name=monitor_config["queue_name"],
        response_key_prefix=monitor_config["response_key_prefix"],
        summarizer_queue=monitor_config["summarizer_queue"],
        process_audio_function=monitor_config["process_audio_function"],
        logger_name=name,
    )

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
        ],
        monitor_config={
            "bot_logic": derf_bot,
            "queue_name": "voice_response_queue",
            "response_key_prefix": "response_queue",
            "summarizer_queue": "summarizer_queue",
            "process_audio_function": process_derf,
        },
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
        ],
        monitor_config={
            "bot_logic": nicole_bot,
            "queue_name": "voice_nic_response_queue",
            "response_key_prefix": "response_nic_queue",
            "summarizer_queue": "summarizer_nic_queue",
            "process_audio_function": process_nic,
        },
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
