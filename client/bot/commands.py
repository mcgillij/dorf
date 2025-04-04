from random import choice

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.processing import (
    queue_message_processing,
    queue_nic_message_processing,
    process_response,
    process_nic_response,
)

from bot.utilities import (
    filtered_responses,
    filter_message,
    logger,
)

from bot.lms import (
    search_with_tool,
)

from bot.audio_capture import RingBufferAudioSink, connect_to_voice, start_capture

load_dotenv()

# Configure bot and intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.members = True
bot = commands.Bot(command_prefix="!", intents=INTENTS)
nic_bot = commands.Bot(command_prefix="#", intents=INTENTS)


# Command to handle messages
@bot.command()
async def search(ctx, *, message: str):
    results = await search_with_tool(message)
    await ctx.send(results)


@bot.command()
async def derf(ctx, *, message: str):
    # Check if the message contains any filtered keywords
    if filter_message(message):
        await ctx.send(choice(filtered_responses))
        return

    unique_id = await queue_message_processing(ctx, message)
    await process_response(ctx, unique_id)


@nic_bot.command()
async def nic(ctx, *, message: str):
    unique_id = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, unique_id)


@bot.command()
async def check_bots(ctx):
    # Ensure the user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("You must be in a voice channel first.")

    channel = ctx.author.voice.channel
    bots = [member for member in channel.members if member.bot]

    if bots:
        bot_names = ", ".join([m.name for m in bots])
        await ctx.send(f"Bots found: {bot_names}")
    else:
        await ctx.send("No bots detected here.")


@nic_bot.event
@bot.event
async def on_voice_state_update(member, before, after):
    for b in [bot, nic_bot]:
        logger.info(
            f"Voice state update: {member} | Before: {before.channel} | After: {after.channel}"
        )

        if member == b.user:
            # Handle bot disconnection case
            if not after.channel and before.channel:
                logger.warning("Detected disconnection. Attempting to reconnect...")
                await connect_to_voice(b)
                return

            # Added logic for when the bot connects/joins a new channel
            elif after.channel and not before.channel:  # Bot just joined this channel
                guild = member.guild  # Guild where the bot is joining
                logger.info(
                    f"Bot has connected to {after.channel} in guild {guild}. Starting capture."
                )
                await start_capture(guild, after.channel)
                return

        # Existing logic for member joins:
        if after.channel and not before.channel:
            await start_capture(member.guild, after.channel)

        # Handle self mute/deaf changes
        if (before.self_mute != after.self_mute) or (
            before.self_deaf != after.self_deaf
        ):
            vc = member.guild.voice_client
            if vc and vc.is_listening():
                logger.info(f"Voice state changed for {member}")
                sink = vc.sink
                if isinstance(sink, RingBufferAudioSink):
                    await b.loop.run_in_executor(None, sink.save_user_audio, member.id)
