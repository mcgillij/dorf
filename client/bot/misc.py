import asyncio
import json
import logging
import time

import dice
import discord
from discord.ext import commands
from bot.utilities import get_random_image_path, split_message
from bot.constants import FRIEREN_DIR, WHISPER_DEAD_QUEUE, VOICE_RESPONSE_DEAD_QUEUE
from bot.redis_client import redis_client
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


class MiscCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.launch_time = discord.utils.utcnow()
        # channel_id -> last "chup" reply timestamp (per-channel rate limit)
        self._last_chup_reply = {}

    @commands.command(name="list", aliases=["commands"])
    async def list_commands(self, ctx):
        """Lists all commands, their parameter combinations, and aliases."""
        command_details = []
        for cmd in self.bot.commands:
            params = ", ".join(cmd.clean_params.keys())
            aliases = ", ".join(cmd.aliases) if cmd.aliases else "None"
            command_details.append(f"!{cmd.name}({params}) - Aliases: [{aliases}]")
        # One message per 2000 chars: !list used to exceed Discord's limit
        # and raise once the command count grew.
        text = "Available commands:\n" + "\n".join(command_details)
        for chunk in split_message(text):
            await ctx.send(chunk)

    @commands.command(name="deadletters")
    @commands.has_permissions(manage_messages=True)
    async def deadletters(self, ctx):
        """Admin: inspect the dead-letter queues (failed STT/voice jobs).
        They used to be write-only — postmortems meant digging through Redis."""

        def _snapshot():
            out = []
            for name, queue in (
                ("whisper (STT)", WHISPER_DEAD_QUEUE),
                ("voice (TTS/playback)", VOICE_RESPONSE_DEAD_QUEUE),
            ):
                depth = redis_client.llen(queue)
                samples = redis_client.lrange(queue, 0, 4)
                out.append((name, depth, samples))
            return out

        lines = []
        for name, depth, samples in await asyncio.to_thread(_snapshot):
            lines.append(f"**{name}**: {depth} dead job(s)")
            for raw in samples:
                try:
                    job = json.loads(raw)
                except ValueError:
                    lines.append(f"> `{str(raw)[:80]}`")
                    continue
                lines.append(
                    f"> `{str(job.get('trace_id') or job.get('unique_id') or '?')[:12]}` "
                    f"reason={job.get('dead_reason', '?')} "
                    f"attempts={job.get('attempt', '?')}"
                )
        await ctx.send("\n".join(lines))

    @commands.command()
    async def uptime(self, ctx):
        """Displays the bot's uptime."""
        delta_uptime = discord.utils.utcnow() - self.bot.launch_time
        hours, remainder = divmod(int(delta_uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        await ctx.send(f"Uptime: {hours}h {minutes}m {seconds}s")

    @commands.command()
    async def check_bots(self, ctx):
        """Check the bots voicechat connectivity"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("You must be in a voice channel first.")
        bots = [m.name for m in ctx.author.voice.channel.members if m.bot]
        await ctx.send(
            f"Bots found: {', '.join(bots) if bots else 'No bots detected.'}"
        )

    @commands.command()
    async def frieren(self, ctx):
        """Sends a random image from the frieren directory."""
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

    @commands.Cog.listener()
    async def on_message(self, message):

        if message.author.bot:
            return  # Ignore bot messages

        content = message.content.strip()

        # "chup" retort: fuzz.partial_ratio fires on any message that merely
        # contains a near-substring, so long unrelated messages triggered it.
        # Only short messages count, and reply at most once per 30s/channel.
        if len(content) <= 25 and fuzz.partial_ratio(content.lower(), "chup") > 85:
            now = time.monotonic()
            if now - self._last_chup_reply.get(message.channel.id, 0.0) > 30:
                self._last_chup_reply[message.channel.id] = now
                if len(self._last_chup_reply) > 100:
                    self._last_chup_reply.clear()
                await message.channel.send("NO U CHUP!")
                return

    @commands.command()
    async def marne(self, ctx):
        """send the url to spackmarne.com"""
        await ctx.send("<https://spackmarne.com>")

    @commands.command(name="roll", aliases=["r"])
    async def roll_dice(self, ctx, *, dice_notation: str):
        """Rolls dice using standard dice notation (e.g., !roll d20, !r 2d8+4)."""

        clean_notation = dice_notation.strip()  # Remove leading/trailing whitespace

        if not clean_notation:
            await ctx.send("Usage: `!r <dice_notation>` (e.g., `!roll 2d6+3`)")
            return

        logger.info(f"Dice roll requested by {ctx.author}: {clean_notation}")

        try:
            # The dice library handles parsing the notation string
            result = dice.roll(clean_notation)

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

        except dice.DiceBaseException as e:
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
            await ctx.send(
                "An unexpected error occurred while trying to roll the dice."
            )


async def setup(bot):
    await bot.add_cog(MiscCog(bot))
    logger.info("MISC Cog loaded successfully.")
