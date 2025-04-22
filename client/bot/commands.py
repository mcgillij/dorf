import os
from random import choice
import asyncio
from collections import defaultdict
import sqlite3
import re

import dice
import emoji
import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient

from dotenv import load_dotenv
from bot.poll import PollView, active_polls

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

from bot.quotes import addquote, listquotes, quote, deletequote, searchquote

CUSTOM_EMOJI_REGEX = re.compile(r"<a?:\w+:\d+>")
EMOJI_DB = "emojis.db"

conn = sqlite3.connect(EMOJI_DB)
c = conn.cursor()

# Ensure quotes table exists
c.execute(
    """
    CREATE TABLE IF NOT EXISTS emoji_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    usage_count INTEGER DEFAULT 1,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, emoji)
);
"""
)
conn.commit()

load_dotenv()

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
if not AUTH_TOKEN:
    raise ValueError(
        "AUTH_TOKEN is missing. Please set it in the environment variables."
    )
WORKSPACE = "birthright"
# WORKSPACE = "a-new-workspace"
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


@commands.command()
async def marne(ctx):
    """send the url to spackmarne.com"""
    await ctx.send("<https://spackmarne.com>")


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


@commands.command(name="poll", aliases=["p"])
async def poll(ctx, *, question: str):
    """Create a poll that automatically ends after 5 minutes."""
    poll_id = str(ctx.message.id)
    active_polls[poll_id] = {
        "question": question,
        "yes": 0,
        "no": 0,
        "channel_id": ctx.channel.id,
        "message_id": None,
    }

    embed = discord.Embed(
        title="📊 New Poll!",
        description=f"{question}\n\n*Poll ends in 5 minutes!*",
        color=discord.Color.blue(),
    )
    view = PollView(poll_id)
    message = await ctx.send(embed=embed, view=view)

    active_polls[poll_id]["message_id"] = message.id

    # Start background task
    bot.loop.create_task(close_poll_after_delay(poll_id, view))


async def close_poll_after_delay(poll_id, view):
    await asyncio.sleep(300)  # 5 minutes
    view.close_poll()

    poll = active_polls.get(poll_id)
    if not poll:
        return

    channel = bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        return

    # Update the embed to show "Poll Ended"
    embed = discord.Embed(
        title="📋 Poll Ended!",
        description=f"**{poll['question']}**\n\n👍 Yes: {poll['yes']}\n👎 No: {poll['no']}",
        color=discord.Color.gold(),
    )
    await message.edit(embed=embed, view=view)

    # Clean up
    active_polls.pop(poll_id, None)


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


@commands.command(name="search", aliases=["s"])
async def search(ctx, *, message: str):
    logger.info("in search")

    def callback(param=None):
        logger.info("in callback")
        if param:
            enqueue_message(ctx.channel, param)

    results = await search_with_tool(message, callback)
    for msg in split_message(results):
        enqueue_message(ctx.channel, msg)


@commands.command()
async def nic(ctx, *, message: str):
    uid = await queue_nic_message_processing(ctx, message)
    await process_nic_response(ctx, uid)


class NicBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="nic_bot", prefix="#", *args, **kwargs)
        self.add_command(nic)
        self.llm = LLMClient(AUTH_TOKEN, NIC_WORKSPACE, NIC_SESSION_ID)


class DerfBot(BaseBot):
    def __init__(self, *args, **kwargs):
        super().__init__(name="derfbot", prefix="!", *args, **kwargs)
        self.add_command(search)
        logger.info("attaching derf command")
        self.add_command(commands.Command(roll_dice, name="roll", aliases=["r"]))
        self.add_command(derf)
        self.add_command(spack)
        self.add_command(frieren)
        self.add_command(poll)
        self.add_command(marne)
        self.add_command(quote)
        self.add_command(addquote)
        self.add_command(listquotes)
        self.add_command(searchquote)
        self.add_command(deletequote)
        self.add_command(
            commands.Command(emojistats, name="emojistats", aliases=["es"])
        )
        self.add_command(
            commands.Command(emoji_leaderboard, name="emojileaderboard", aliases=["el"])
        )
        self.add_listener(self.on_voice_state_update)
        self.add_listener(on_message)
        self.add_listener(on_reaction_add)
        self.llm = LLMClient(AUTH_TOKEN, WORKSPACE, SESSION_ID)

    async def handle_voice_state_update(self, member, before, after):
        logger.info(
            f"{self.name}: Voice update for {member} | {before.channel} -> {after.channel}"
        )

        if member == self.user:
            if after.channel and not before.channel:
                logger.info(f"{self.name} joined a voice channel, starting capture.")
                await start_capture(member.guild, after.channel, self)
            elif not after.channel and before.channel:
                logger.warning(f"{self.name} disconnected. Reconnecting...")
                await connect_to_voice(self)
            return

        # For regular users:
        if after.channel and not before.channel:
            logger.info(f"User {member} joined a voice channel.")
            if member.guild.voice_client:
                # If bot is already connected, maybe do something
                logger.info(f"Bot already connected, ensuring capture is active.")
                await start_capture(member.guild, after.channel, self)

    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            # Ignore bot events
            return
        await self.handle_voice_state_update(member=member, before=before, after=after)

    async def on_ready(self):
        await super().on_ready()
        logger.info(f"{self.name} is ready. Checking voice connections...")
        for guild in self.guilds:
            voice_channel = discord.utils.get(
                guild.voice_channels, id=int(os.getenv("VOICE_CHANNEL_ID", 0))
            )
            if voice_channel:
                await start_capture(guild, voice_channel, self)


async def emoji_leaderboard(ctx, top_n: int = 10):
    with sqlite3.connect(EMOJI_DB) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT user_id, emoji, SUM(usage_count) as total_usage
            FROM emoji_usage
            GROUP BY user_id, emoji
            ORDER BY total_usage DESC
            LIMIT ?
        """,
            (top_n,),
        )
        rows = c.fetchall()

    if not rows:
        await ctx.send("No emoji data yet! 😢")
        return

    # Build user stats
    user_emoji_stats = defaultdict(list)
    for user_id, emoji_used, count in rows:
        user_emoji_stats[user_id].append((emoji_used, count))

    # Fetch usernames
    leaderboard_entries = []
    for user_id, emoji_stats in user_emoji_stats.items():
        user = await bot.fetch_user(user_id)
        username = user.display_name if user else f"User {user_id}"

        total_user_usage = sum(count for _, count in emoji_stats)
        top_emojis = sorted(emoji_stats, key=lambda x: -x[1])[:3]

        emojis_display = " ".join(f"{emj}({cnt})" for emj, cnt in top_emojis)

        leaderboard_entries.append((username, total_user_usage, emojis_display))

    # Sort leaderboard
    leaderboard_entries.sort(key=lambda x: -x[1])

    # Build fancy text
    medal_emojis = ["🥇", "🥈", "🥉"]
    response_lines = []
    total_all_usage = sum(entry[1] for entry in leaderboard_entries)

    for idx, (username, total_usage, emojis_display) in enumerate(leaderboard_entries):
        medal = medal_emojis[idx] if idx < 3 else f"`#{idx+1}`"

        # Bar graph
        percentage = (total_usage / total_all_usage) * 100 if total_all_usage else 0
        bars = "█" * int(percentage // 5)

        line = f"{medal} **{username}** - {total_usage} uses | {bars} {percentage:.1f}%\nTop: {emojis_display}"
        response_lines.append(line)

    embed = discord.Embed(
        title="🏆 Emoji Leaderboard",
        description="\n\n".join(response_lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Tracking all emoji usage across chat and reactions!")

    await ctx.send(embed=embed)


async def start_capture(guild, channel, bot):
    logger.info(f"Starting capture in {channel.name} for bot {bot.name}")
    try:
        vc = guild.voice_client
        if vc is None or not vc.is_connected():
            logger.info("Not connected yet. Connecting to voice...")
            vc = await channel.connect(cls=VoiceRecvClient)

        if vc is None or not vc.is_connected():
            logger.error("Failed to connect to voice, aborting capture.")
            return

        if vc.is_listening():
            logger.info("Already listening, resetting sink...")
            vc.stop_listening()

        ring_buffer_sink = RingBufferAudioSink(bot=bot, buffer_size=1024 * 1024)
        vc.listen(ring_buffer_sink)
        logger.info(f"Recording started in channel {channel.name}")
        logger.info(f"Sweeping channel {channel.name} for existing members...")
        for member in channel.members:
            if member.bot:
                continue
            logger.info(
                f"Detected existing member {member.display_name}. Initializing capture."
            )
            await bot.handle_voice_state_update(member, None, member.voice)

    except Exception as e:
        logger.error(f"Error in start_capture: {e}")


async def connect_to_voice(b):
    try:
        guild_id = int(os.getenv("GUILD_ID", ""))
        voice_channel_id = int(os.getenv("VOICE_CHANNEL_ID", ""))
    except ValueError:
        logger.error("GUILD_ID or VOICE_CHANNEL_ID is not a valid integer.")
        return

    if not guild_id or not voice_channel_id:
        logger.error(
            "GUILD_ID or VOICE_CHANNEL_ID is missing in the environment variables."
        )
        return
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
            entry.name
            for entry in os.scandir(directory)
            if entry.is_file()
            and entry.name.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
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


def extract_emojis(text):
    found = []

    # 1. Find unicode emojis
    for character in text:
        if emoji.is_emoji(character):
            found.append(character)

    # 2. Find custom emojis
    custom_matches = CUSTOM_EMOJI_REGEX.findall(text)
    for match in custom_matches:
        found.append(match)

    return found


async def on_message(message):
    if message.author.bot:
        return

    emojis = extract_emojis(message.content)
    if emojis:
        with sqlite3.connect(EMOJI_DB) as conn:
            c = conn.cursor()
            for em in emojis:
                c.execute(
                    """
                    INSERT INTO emoji_usage (user_id, emoji, usage_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(user_id, emoji)
                    DO UPDATE SET usage_count = usage_count + 1, last_used = CURRENT_TIMESTAMP
                """,
                    (message.author.id, em),
                )
            conn.commit()
    # await bot.process_commands(message)  # important to not block commands


async def on_reaction_add(reaction, user):
    if user.bot:
        return

    # Get the emoji as a string
    emoji_used = str(reaction.emoji)

    with sqlite3.connect(EMOJI_DB) as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO emoji_usage (user_id, emoji, usage_count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, emoji)
            DO UPDATE SET usage_count = usage_count + 1, last_used = CURRENT_TIMESTAMP
        """,
            (user.id, emoji_used),
        )
        conn.commit()


async def emojistats(ctx, user: discord.User = None):
    """Show emoji usage stats for a user (or yourself)."""
    user = user or ctx.author
    with sqlite3.connect(EMOJI_DB) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT emoji, usage_count FROM emoji_usage
            WHERE user_id = ?
            ORDER BY usage_count DESC
            LIMIT 10
        """,
            (user.id,),
        )
        results = c.fetchall()

    if not results:
        await ctx.send(f"{user.display_name} hasn't used any emojis yet!")
        return

    stats = "\n".join([f"{emoji} — {count} times" for emoji, count in results])
    await ctx.send(f"**Top emojis for {user.display_name}:**\n{stats}")


# instantiate my bots here temporarily, need to refactor this
bot = DerfBot()
nic_bot = NicBot()
