import os
from random import choice
import asyncio
from asyncio import run_coroutine_threadsafe
import dice

import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient

from dotenv import load_dotenv

from bot.processing import (
    queue_derf_message_processing,
    queue_nic_message_processing,
    process_derf_response,
    process_nic_response,
)

from bot.utilities import filtered_responses, filter_message, split_message

from bot.log_config import setup_logger

from bot.lms import (
    search_with_tool,
)
from bot.utilities import LLMClient
from bot.audio_capture import RingBufferAudioSink

load_dotenv()

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
# WORKSPACE = "birthright"
WORKSPACE = "a-new-workspace"
NIC_WORKSPACE = "nic"
SESSION_ID = "my-session-id"
NIC_SESSION_ID = "my-session-id"

logger = setup_logger(__name__)

# Configure bot and intents
INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.voice_states = True
INTENTS.members = True

SPACK_DIR = "spack/"
FRIEREN_DIR = "frieren/"

# message queue to not get rate limited hopefully by discord
message_queue = asyncio.Queue()


# Background task to process the queue
async def message_dispatcher():
    await bot.wait_until_ready()
    while not bot.is_closed():
        channel, content = await message_queue.get()
        try:
            await channel.send(content)
        except Exception as e:
            print(f"Failed to send message: {e}")
        await asyncio.sleep(1)  # Adjust this to control rate


# Enqueue a message
def enqueue_message(channel, content):
    message_queue.put_nowait((channel, content))


@commands.command()
async def check_bots(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("You must be in a voice channel first.")
    bots = [m.name for m in ctx.author.voice.channel.members if m.bot]
    await ctx.send(f"Bots found: {', '.join(bots) if bots else 'No bots detected.'}")


@commands.command()
async def spack(ctx):
    """Sends a random image from the images directory."""
    image_path = get_random_image_path(SPACK_DIR)
    logger.info(f"Image path: {image_path}")
    if image_path:
        try:
            with open(image_path, "rb") as f:
                picture = discord.File(f)
                await ctx.send(file=picture)
        except FileNotFoundError:
            await ctx.send("Image file not found (even though path was generated).")
    else:
        await ctx.send(f"No images found in the '{SPACK_DIR}' directory.")


@commands.command()
async def frieren(ctx):
    """Sends a random image from the images directory."""
    image_path = get_random_image_path(FRIEREN_DIR)
    logger.info(f"Image path: {image_path}")
    if image_path:
        try:
            with open(image_path, "rb") as f:
                picture = discord.File(f)
                await ctx.send(file=picture)
        except FileNotFoundError:
            await ctx.send("Image file not found (even though path was generated).")
    else:
        await ctx.send(f"No images found in the '{FRIEREN_DIR}' directory.")


class BaseBot(commands.Bot):
    def __init__(self, name, prefix, *args, **kwargs):
        super().__init__(command_prefix=prefix, intents=INTENTS, *args, **kwargs)
        self.name = name
        self.add_listener(self.on_ready)
        self.add_command(check_bots)

    async def on_ready(self):
        logger.info(f"{self.name} is ready.")


@commands.command()
async def derf(ctx, *, message: str):
    logger.info("in derf")
    if filter_message(message):
        await ctx.send(choice(filtered_responses))
        return
    uid = await queue_derf_message_processing(ctx, message)
    await process_derf_response(ctx, uid)


@commands.command(name="roll", aliases=["r"])  # Command name is !roll, alias !r
async def roll_dice(ctx, *, dice_notation: str):
    """Rolls dice using standard dice notation (e.g., !roll d20, !r 2d8+4)."""

    clean_notation = dice_notation.strip()  # Remove leading/trailing whitespace

    if not clean_notation:
        await ctx.send(f"Usage: `!r <dice_notation>` (e.g., `!roll 2d6+3`)")
        return

    logger.info(f"Dice roll requested by {ctx.author}: {clean_notation}")

    try:
        # The dice library handles parsing the notation string
        result = dice.roll(clean_notation)

        # Format the result message (similar to your on_message logic)
        result_message = ""
        if isinstance(result, (int, float)):
            result_message = f"**{result}**"
        elif isinstance(result, list) and len(result) == 1:
            result_message = f"**{result[0]}**"
        elif isinstance(result, list):
            result_message = f"{str(result)}: **{sum(result)}**"
        else:  # Fallback for any other types dice might return
            result_message = f"{str(result)}"

        # Send the result back using the command context
        await ctx.send(
            f":game_die: {ctx.author.mention} rolled `{clean_notation}`: {result_message}"
        )

    except (dice.DiceBaseException, dice.DiceFatalError) as e:
        logger.warning(
            f"Invalid dice notation from {ctx.author}: '{clean_notation}'. Error: {e}"
        )
        await ctx.send(
            f"Sorry {ctx.author.mention}, I couldn't understand `{clean_notation}`. Please use standard dice notation (like `d20`, `2d6+3`). Error: {e}"
        )
    except Exception as e:
        # Catch any other unexpected errors during rolling
        logger.error(
            f"Unexpected error rolling dice '{clean_notation}' for {ctx.author}: {e}",
            exc_info=True,
        )
        await ctx.send("An unexpected error occurred while trying to roll the dice.")


@commands.command(name="search", alias="s")
async def search(ctx, *, message: str):
    logger.info("in search")

    def callback(param=None):
        logger.info("in callback")
        if param:
            enqueue_message(ctx.channel, param)

    results = await search_with_tool(message, callback)
    for msg in split_message(results):
        enqueue_message(ctx.channel, msg)


class DerfBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="derfbot", prefix="!", *args, **kwargs)
        self.add_command(search)
        self.add_command(roll_dice)
        logger.info("attaching derf command")
        self.add_command(derf)
        self.add_command(spack)
        self.add_command(frieren)
        self.add_listener(self.on_voice_state_update)
        self.llm = LLMClient(AUTH_TOKEN, WORKSPACE, SESSION_ID)

    async def on_voice_state_update(self, member, before, after):
        await handle_voice_state_update(self, member, before, after)


@commands.command()
async def nic(ctx, *, message: str):
    uid = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, uid)


class NicBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="nic_bot", prefix="#", *args, **kwargs)
        self.add_command(nic)
        self.llm = LLMClient(AUTH_TOKEN, NIC_WORKSPACE, NIC_SESSION_ID)


# shared by the bots
async def handle_voice_state_update(bot, member, before, after):
    logger.info(
        f"{bot.name}: Voice update for {member} | {before.channel} -> {after.channel}"
    )

    if member == bot.user:
        if not after.channel and before.channel:
            logger.warning(f"{bot.name} disconnected. Reconnecting...")
            await connect_to_voice(bot)
        elif after.channel and not before.channel:
            await start_capture(member.guild, after.channel, bot)
    elif after.channel and not before.channel:
        await start_capture(member.guild, after.channel, bot)


async def start_capture(guild, channel, b):
    logger.info(f"in: {b=}")
    try:
        vc = guild.voice_client
        if not vc or vc.channel != channel:
            vc = await channel.connect(cls=VoiceRecvClient)

        if vc.is_listening():
            logger.info("Already capturing audio.")
            return

        ring_buffer_sink = RingBufferAudioSink(bot=b, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        logger.info(f"Recording started in channel {channel}")
    except Exception as e:
        logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(b):
    guild_id = int(os.getenv("GUILD_ID", ""))
    voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    guild = discord.utils.get(b.guilds, id=guild_id)

    if not guild:
        logger.error("Guild not found.")
        return

    voice_channel = guild.get_channel(voice_channel_id)
    if not isinstance(voice_channel, discord.VoiceChannel):
        logger.error(f"Invalid or non-existent voice channel: {voice_channel_id}")
        return

    # Check existing connections in the guild
    current_vc = next((vc for vc in b.voice_clients if vc.guild == guild), None)

    try:
        if not current_vc:
            await voice_channel.connect(cls=VoiceRecvClient)
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
                    await voice_channel.connect(cls=VoiceRecvClient)
            else:
                logger.debug("Already connected to the correct channel.")
    except Exception as e:
        logger.exception(f"Connection error: {str(e)}")


def get_random_image_path(directory):
    """
    Returns a random image file path from the specified directory.

    Args:
        directory (str): The path to the directory containing images.

    Returns:
        str: The full path to a randomly selected image file, or None if no images are found.
    """
    try:
        image_files = [
            f
            for f in os.listdir(directory)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
        ]  # Filter for common image extensions

        if not image_files:
            print(f"No images found in directory: {directory}")  # helpful debug message
            return None

        random_image = choice(image_files)
        return os.path.join(directory, random_image)  # Construct the full path
    except FileNotFoundError:
        print(f"Directory not found: {directory}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")  # Catch other potential errors
        return None


# instantiate my bots here temporarily, need to refactor this
bot = DerfBot()
nic_bot = NicBot()
