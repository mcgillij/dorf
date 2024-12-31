import os
import discord
from bot.commands import bot

async def connect_to_voice_channel_on_ready():
    guild_id = int(os.getenv("GUILD_ID", ""))
    #print(f"Guild ID: {guild_id}")
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    #print(f"Voice Channel ID: {voice_channel_id}")
    guild = discord.utils.get(bot.guilds, id=guild_id)
    print("Connecting to voice channel...")
    if guild:
        voice_channel = discord.utils.get(guild.voice_channels, id=voice_channel_id)
        if voice_channel:
            try:
                await voice_channel.connect()
                print(f"Connected to voice channel: {voice_channel.name}")
            except discord.ClientException as e:
                print(f"Already connected or error connecting: {e}")
            except Exception as e:
                print(f"Unexpected error connecting to voice channel: {e}")
        else:
            print("Voice channel not found.")
    else:
        print("Guild not found.")

