import io
import sqlite3
from datetime import datetime, timedelta, timezone
import logging

import discord
from discord.ext import commands, tasks

import matplotlib.pyplot as plt
import pandas as pd

from bot.constants import METRICS_DB, EMOJI_DB

logger = logging.getLogger(__name__)


class Metrics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ensure_tables()
        self.aggregate_metrics.start()
        self.user_cache = {}
        self.channel_cache = {}

    def ensure_tables(self):
        with sqlite3.connect(METRICS_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    name TEXT,
                    user_id INTEGER,
                    channel_id INTEGER,
                    guild_id INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS weekly_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    week TEXT,
                    type TEXT,
                    name TEXT,
                    count INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            conn.commit()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        with sqlite3.connect(METRICS_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO bot_usage (type, user_id, channel_id, guild_id)
                VALUES (?, ?, ?, ?)
            """,
                (
                    "message",
                    message.author.id,
                    message.channel.id,
                    getattr(message.guild, "id", None),
                ),
            )
            conn.commit()

    @commands.Cog.listener()
    async def on_command(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO bot_usage (type, name, user_id, channel_id, guild_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    "command",
                    ctx.command.name,
                    ctx.author.id,
                    ctx.channel.id,
                    getattr(ctx.guild, "id", None),
                ),
            )
            conn.commit()

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return

        with sqlite3.connect(METRICS_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO bot_usage (type, name, user_id, channel_id, guild_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    "reaction",
                    str(reaction.emoji),
                    user.id,
                    reaction.message.channel.id,
                    getattr(reaction.message.guild, "id", None),
                ),
            )
            conn.commit()

    @tasks.loop(hours=24)
    async def aggregate_metrics(self):
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        with sqlite3.connect(METRICS_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT strftime('%Y-%W', timestamp) as week, type, name, COUNT(*) as count
                FROM bot_usage
                WHERE timestamp <= ?
                GROUP BY week, type, name
            """,
                (one_week_ago.isoformat(),),
            )
            results = c.fetchall()

            for week, typ, name, count in results:
                c.execute(
                    """
                    INSERT INTO weekly_metrics (week, type, name, count)
                    VALUES (?, ?, ?, ?)
                """,
                    (week, typ, name, count),
                )

            c.execute(
                "DELETE FROM bot_usage WHERE timestamp <= ?",
                (one_week_ago.isoformat(),),
            )
            conn.commit()

    @aggregate_metrics.before_loop
    async def before_aggregate_metrics(self):
        await self.bot.wait_until_ready()

    def create_plot_and_send(self, ctx, df, title, xlabel, ylabel):
        plt.figure(figsize=(10, 6))
        df.plot()
        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        return discord.File(buf, filename="plot.png")

    @commands.command(name="emoji_usage")
    async def emoji_usage(self, ctx):
        """Show overall emoji usage metrics."""
        with sqlite3.connect(EMOJI_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT emoji, SUM(usage_count) as total_usage
                FROM emoji_usage
                GROUP BY emoji
                ORDER BY total_usage DESC
                LIMIT 10
                """,
                conn,
            )
        df.set_index("emoji", inplace=True)
        file = self.create_plot_and_send(ctx, df, "Top Emojis", "Emoji", "Usage Count")
        await ctx.send(file=file)

    @commands.command(name="emoji_trends")
    async def emoji_trends(self, ctx, emoji_char: str):
        """Show usage trends for a specific emoji."""
        with sqlite3.connect(EMOJI_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT date(last_used) as day, SUM(usage_count) as count
                FROM emoji_usage
                WHERE emoji = ?
                GROUP BY day
                ORDER BY day ASC
                """,
                conn,
                params=(emoji_char,),
            )
        df.set_index("day", inplace=True)
        file = self.create_plot_and_send(
            ctx, df, f"Usage Trend: {emoji_char}", "Date", "Usage Count"
        )
        await ctx.send(file=file)

    @commands.command(name="activity_over_time")
    async def activity_over_time(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT date(timestamp) as day, type, COUNT(*) as count
                FROM bot_usage
                GROUP BY day, type
                ORDER BY day ASC
            """,
                conn,
            )
        pivot = df.pivot(index="day", columns="type", values="count").fillna(0)
        file = self.create_plot_and_send(
            ctx, pivot, "Bot Activity Over Time", "Date", "Count"
        )
        await ctx.send(file=file)

    @commands.command(name="top_users")
    async def top_users(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT user_id, type, COUNT(*) as count
                FROM bot_usage
                GROUP BY user_id, type
            """,
                conn,
            )

        top = df.groupby("user_id")["count"].sum().sort_values(ascending=False).head(10)

        # Resolve user_ids to display names (with caching)
        display_names = []
        for user_id in top.index:
            if user_id in self.user_cache:
                name = self.user_cache[user_id]
            else:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                name = user.display_name if hasattr(user, "display_name") else user.name
                self.user_cache[user_id] = name
            display_names.append(name)

        top.index = display_names  # Replace user_id with display names

        top.plot(kind="bar", figsize=(10, 6), title="Top Users by Total Activity")
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        await ctx.send(file=discord.File(buf, filename="top_users.png"))

    @commands.command(name="channel_breakdown")
    async def channel_breakdown(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT channel_id, type, COUNT(*) as count
                FROM bot_usage
                GROUP BY channel_id, type
            """,
                conn,
            )

        # Get top 10 by total activity
        totals = (
            df.groupby("channel_id")["count"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        top_ids = totals.index.tolist()
        df = df[df["channel_id"].isin(top_ids)]

        # Pivot for plotting
        pivot = df.pivot(index="channel_id", columns="type", values="count").fillna(0)

        # Replace IDs with names using cache
        display_names = []
        for cid in pivot.index:
            if cid in self.channel_cache:
                name = self.channel_cache[cid]
            else:
                channel = self.bot.get_channel(cid)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(cid)
                    except discord.NotFound:
                        channel = None
                name = channel.name if channel else f"Unknown ({cid})"
                self.channel_cache[cid] = name
            display_names.append(name)

        pivot.index = display_names

        file = self.create_plot_and_send(
            ctx, pivot, "Top Channels by Type", "Channel", "Count"
        )
        await ctx.send(file=file)

    @commands.command(name="command_usage")
    async def command_usage(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT name, COUNT(*) as count FROM bot_usage
                WHERE type = 'command'
                GROUP BY name ORDER BY count DESC LIMIT 10
            """,
                conn,
            )
        df.set_index("name", inplace=True)
        file = self.create_plot_and_send(ctx, df, "Top Commands", "Command", "Count")
        await ctx.send(file=file)

    @commands.command(name="weekly_summary")
    async def weekly_summary(self, ctx):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT week, type, SUM(count) as total FROM weekly_metrics
                GROUP BY week, type ORDER BY week ASC
            """,
                conn,
            )
        pivot = df.pivot(index="week", columns="type", values="total").fillna(0)
        file = self.create_plot_and_send(
            ctx, pivot, "Weekly Bot Summary", "Week", "Total"
        )
        await ctx.send(file=file)

    @commands.command(name="command_trends")
    async def command_trends(self, ctx, command_name):
        with sqlite3.connect(METRICS_DB) as conn:
            df = pd.read_sql_query(
                """
                SELECT date(timestamp) as day, COUNT(*) as count FROM bot_usage
                WHERE type = 'command' AND name = ?
                GROUP BY day ORDER BY day ASC
            """,
                conn,
                params=(command_name,),
            )
        df.set_index("day", inplace=True)
        file = self.create_plot_and_send(
            ctx, df, f"Usage Trend: {command_name}", "Date", "Count"
        )
        await ctx.send(file=file)


async def setup(bot):
    await bot.add_cog(Metrics(bot))
    logger.info("Metrics cog loaded.")
