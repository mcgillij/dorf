import asyncio
import logging
from typing import List, Awaitable, Callable

from bot.commands import DerfBot, NicBot
from bot.commands import connect_to_voice

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


logger = logging.getLogger(__name__)

# Create bots
derf_bot = DerfBot()
nic_bot = NicBot()


async def setup_bot(
    bot_instance,
    name: str,
    voice_connector,
    worker_tasks: List[Callable[[], Awaitable[None]]],
):
    await bot_instance.wait_until_ready()
    logger.info(f"{name} is ready: Logged in as {bot_instance.user.name}")
    await voice_connector(bot_instance)

    for task in worker_tasks:
        asyncio.create_task(task())

    logger.info(f"{name} startup complete")


async def derfbot_ready():
    await derf_bot.load_extension("bot.leveling")
    await derf_bot.load_extension("bot.adventure")
    await derf_bot.load_extension("bot.quotes")
    await derf_bot.load_extension("bot.emoji")
    await derf_bot.load_extension("bot.poll")
    await derf_bot.load_extension("bot.misc")
    await derf_bot.load_extension("bot.search")
    await derf_bot.load_extension("bot.macro")
    await derf_bot.load_extension("bot.faction")
    await derf_bot.load_extension("bot.combo")
    await derf_bot.load_extension("bot.metrics")
    await derf_bot.load_extension("bot.sdcog")
    await derf_bot.load_extension("bot.news")
    await derf_bot.load_extension("bot.translate")

    await setup_bot(
        bot_instance=derf_bot,
        name="DerfBot",
        voice_connector=connect_to_voice,
        worker_tasks=[
            lambda: derf_audio_task(derf_bot),
            lambda: playback_derf_task(derf_bot),
            lambda: process_derf_response_queue(derf_bot),
            lambda: process_derf_summarizer_queue(derf_bot),
            lambda: monitor_derf_response_queue(derf_bot),
        ],
    )


async def nicbot_ready():
    await setup_bot(
        bot_instance=nic_bot,
        name="NicBot",
        voice_connector=connect_to_voice,
        worker_tasks=[
            lambda: nic_audio_task(nic_bot),
            lambda: playback_nic_task(nic_bot),
            lambda: process_nic_response_queue(nic_bot),
            lambda: process_nic_summarizer_queue(nic_bot),
            lambda: monitor_nic_response_queue(nic_bot),
        ],
    )


@derf_bot.event
async def on_ready():
    await derfbot_ready()


@nic_bot.event
async def on_ready():
    await nicbot_ready()
