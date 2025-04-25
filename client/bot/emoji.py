from collections import defaultdict
import sqlite3
import re
import logging

import emoji
import discord
from discord.ext import commands

from bot.constants import (
    EMOJI_DB,
)


logger = logging.getLogger(__name__)

CUSTOM_EMOJI_REGEX = re.compile(r"<a?:\w+:\d+>")

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


class EmojiUsageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
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

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
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

    @commands.command(name="emojistats", aliases=["es"])
    async def emojistats(self, ctx, user: discord.User = None):
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

    @commands.command(name="emojileaderboard", aliases=["el"])
    async def emoji_leaderboard(self, ctx, top_n: int = 10):
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
            user = await ctx.bot.fetch_user(user_id)
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

        for idx, (username, total_usage, emojis_display) in enumerate(
            leaderboard_entries
        ):
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


async def setup(bot):
    await bot.add_cog(EmojiUsageCog(bot))
    logger.info("EMOJI Cog loaded successfully.")


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
