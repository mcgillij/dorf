import logging
import os
from random import choice

import dice
import discord
from discord.ext import commands

logger = logging.getLogger(__name__)
from bot.constants import (
    SPACK_DIR,
    FRIEREN_DIR,
)


class MiscCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def check_bots(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("You must be in a voice channel first.")
        bots = [m.name for m in ctx.author.voice.channel.members if m.bot]
        await ctx.send(
            f"Bots found: {', '.join(bots) if bots else 'No bots detected.'}"
        )

    @commands.command()
    async def spack(self, ctx):
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
    async def frieren(self, ctx):
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
    async def marne(self, ctx):
        """send the url to spackmarne.com"""
        await ctx.send("<https://spackmarne.com>")

    async def roll_dice(self, ctx, *, dice_notation: str):
        """Rolls dice using standard dice notation (e.g., !roll d20, !r 2d8+4)."""

        clean_notation = dice_notation.strip()  # Remove leading/trailing whitespace

        if not clean_notation:
            await ctx.send(f"Usage: `!r <dice_notation>` (e.g., `!roll 2d6+3`)")
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
            await ctx.send(
                "An unexpected error occurred while trying to roll the dice."
            )


async def setup(bot):
    await bot.add_cog(MiscCog(bot))
    logger.info("MISC Cog loaded successfully.")


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
