""" Derfbot, the logical iteration of DORFBOT """
import os
import asyncio
import logging

from bot.commands import bot
from workers.process_response_worker import process_response_queue
from workers.process_summarizer_worker import process_summarizer_queue
from workers.mimic_audio_worker import mimic_audio_task
from workers.playback_worker import playback_task
from bot.voice_manager import connect_to_voice_channel_on_ready

logging.basicConfig(level=logging.WARN)
#logging.basicConfig(level=logging.DEBUG)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('------')
    asyncio.create_task(connect_to_voice_channel_on_ready())
    asyncio.create_task(mimic_audio_task())
    asyncio.create_task(playback_task())
    asyncio.create_task(process_response_queue())
    asyncio.create_task(process_summarizer_queue())

if __name__ == '__main__':
    async def main():
        await bot.start(os.getenv("DISCORD_BOT_TOKEN", ""))
    asyncio.run(main())


# Run the bot with your token
#bot.run(os.getenv("DISCORD_BOT_TOKEN", ""))
