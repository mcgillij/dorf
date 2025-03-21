import os
import discord
from discord.ext import voice_recv
from bot.commands import bot
from bot.utilities import logger
from dotenv import load_dotenv

load_dotenv()

async def connect_to_voice():
    guild_id = int(os.getenv("GUILD_ID", ""))
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    guild = discord.utils.get(bot.guilds, id=guild_id)

    if not guild:
        logger.error("Guild not found.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not isinstance(voice_channel, discord.VoiceChannel):
        logger.error(f"Invalid or non-existent voice channel: {voice_channel_id}")
        return

    # Check existing connections in the guild
    current_vc = next((vc for vc in bot.voice_clients if vc.guild == guild), None)

    try:
        if not current_vc:
            await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
            logger.info(f"Connected to {voice_channel.name}")
        else:
            # Check if already connected to the correct channel
            if current_vc.channel.id != voice_channel.id:
                # Move existing client or reconnect?
                try:
                    await current_vc.move_to(voice_channel)  # Attempt move first
                    logger.info(f"Moved to {voice_channel.name}")
                except discord.errors.InvalidData as e:
                    logger.error(f"Move failed: {e}. Reconnecting...")
                    await current_vc.disconnect()
                    await voice_channel.connect(cls=voice_recv.VoiceRecvClient)
            else:
                logger.debug("Already connected to the correct channel.")
    except Exception as e:
        logger.exception(f"Connection error: {str(e)}")

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
