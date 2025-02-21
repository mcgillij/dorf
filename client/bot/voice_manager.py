import os
import discord
from discord.ext import voice_recv
from bot.commands import bot
from bot.utilities import logger


async def connect_to_voice_channel_on_ready():
    guild_id = int(os.getenv("GUILD_ID", ""))
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    guild = discord.utils.get(bot.guilds, id=guild_id)
    logger.info("Connecting to voice channel...")
    if guild:
        voice_channel = discord.utils.get(guild.voice_channels, id=voice_channel_id)
        if voice_channel:
            try:
                await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
                logger.info(f"Connected to voice channel: {voice_channel.name}")
            except discord.ClientException as e:
                logger.error(f"Already connected or error connecting: {e}")
            except Exception as e:
                logger.error(f"Unexpected error connecting to voice channel: {e}")
        else:
            logger.error("Voice channel not found.")
    else:
        logger.error("Guild not found.")
