import sqlite3
import random
import logging
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from bot.constants import FACTION_DB, DEFAULT_FACTIONS
from bot.emoji import extract_emojis

logger = logging.getLogger(__name__)


class FactionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.init_db()
        self.check_war_end.start()

    def init_db(self):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()

            c.execute(
                """
            CREATE TABLE IF NOT EXISTS factions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                symbol TEXT,
                color TEXT
            )
            """
            )

            c.execute(
                """
            CREATE TABLE IF NOT EXISTS user_factions (
                user_id INTEGER PRIMARY KEY,
                faction_id INTEGER,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (faction_id) REFERENCES factions(id)
            )
            """
            )

            c.execute(
                """
            CREATE TABLE IF NOT EXISTS faction_scores (
                faction_id INTEGER,
                emoji TEXT,
                usage_count INTEGER DEFAULT 1,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (faction_id, emoji),
                FOREIGN KEY (faction_id) REFERENCES factions(id)
            )
            """
            )

            c.execute(
                """
            CREATE TABLE IF NOT EXISTS war_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                faction_id INTEGER,
                emoji TEXT,
                usage_count INTEGER,
                FOREIGN KEY (faction_id) REFERENCES factions(id)
            )
            """
            )

            c.execute(
                """
            CREATE TABLE IF NOT EXISTS war_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                started_at TIMESTAMP
            )
            """
            )

            # Seed factions if empty
            c.execute("SELECT COUNT(*) FROM factions")
            if c.fetchone()[0] == 0:
                for faction in DEFAULT_FACTIONS:
                    c.execute(
                        "INSERT INTO factions (name, symbol, color) VALUES (?, ?, ?)",
                        (faction["name"], faction["symbol"], faction["color"]),
                    )

            # Ensure war_state exists
            c.execute(
                "INSERT OR IGNORE INTO war_state (id, started_at) VALUES (1, NULL)"
            )
            conn.commit()

    def assign_faction(self, user_id):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()

            c.execute(
                """
                SELECT factions.id, COUNT(user_factions.user_id) as count
                FROM factions
                LEFT JOIN user_factions ON factions.id = user_factions.faction_id
                GROUP BY factions.id
            """
            )
            faction_counts = c.fetchall()
            min_count = min(fc[1] for fc in faction_counts)
            least_filled = [fc[0] for fc in faction_counts if fc[1] == min_count]
            chosen_faction = random.choice(least_filled)

            c.execute(
                "INSERT INTO user_factions (user_id, faction_id) VALUES (?, ?)",
                (user_id, chosen_faction),
            )
            conn.commit()
            return chosen_faction

    def get_user_faction(self, user_id):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT faction_id FROM user_factions WHERE user_id = ?", (user_id,)
            )
            result = c.fetchone()
            return result[0] if result else None

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return

        # Get the emoji as a string
        emoji = str(reaction.emoji)
        user_id = user.id
        if not emoji:
            return

        faction_id = self.get_user_faction(user_id)
        if not faction_id:
            faction_id = self.assign_faction(user_id)

        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO faction_scores (faction_id, emoji, usage_count)
                VALUES (?, ?, 1)
                ON CONFLICT(faction_id, emoji) DO UPDATE SET
                    usage_count = usage_count + 1,
                    last_used = CURRENT_TIMESTAMP
            """,
                (faction_id, emoji),
            )
            conn.commit()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        user_id = message.author.id
        emojis = extract_emojis(message.content)
        if not emojis:
            return

        faction_id = self.get_user_faction(user_id)
        if not faction_id:
            faction_id = self.assign_faction(user_id)

        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            for emj in emojis:
                c.execute(
                    """
                    INSERT INTO faction_scores (faction_id, emoji, usage_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT(faction_id, emoji) DO UPDATE SET
                        usage_count = usage_count + 1,
                        last_used = CURRENT_TIMESTAMP
                """,
                    (faction_id, emj),
                )
            conn.commit()

    @commands.command(name="factioninfo", aliases=["fi"])
    async def factioninfo(self, ctx):
        user_id = ctx.author.id
        faction_id = self.get_user_faction(user_id)

        if not faction_id:
            faction_id = self.assign_faction(user_id)

        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()

            # Faction details
            c.execute(
                "SELECT name, symbol, color FROM factions WHERE id = ?", (faction_id,)
            )
            name, symbol, color = c.fetchone()

            # Join date
            c.execute(
                "SELECT joined_at FROM user_factions WHERE user_id = ?", (user_id,)
            )
            joined_at_row = c.fetchone()
            joined_at = (
                datetime.fromisoformat(joined_at_row[0]) if joined_at_row else None
            )

            # Faction members
            c.execute(
                "SELECT COUNT(*) FROM user_factions WHERE faction_id = ?", (faction_id,)
            )
            members = c.fetchone()[0]

            # Faction score
            c.execute(
                "SELECT SUM(usage_count) FROM faction_scores WHERE faction_id = ?",
                (faction_id,),
            )
            score = c.fetchone()[0] or 0

        # Optional: Custom flavor/lore for each faction
        faction_flavor = {
            "Atiyas": "🌙 Graceful and wise, shadows are their shield.",
            "Rosies": "🔥 The radiant blaze that never dims.",
            "Booms": "✨ From the void they rise, elusive and fierce.",
        }

        embed = discord.Embed(
            title=f"{symbol} Welcome to **{name}**!",
            description=faction_flavor.get(name, ""),
            color=int(color.replace("#", "0x"), 16),
        )
        embed.add_field(
            name="📅 Joined",
            value=joined_at.strftime("%Y-%m-%d") if joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(name="🧑‍🤝‍🧑 Members", value=str(members), inline=True)
        embed.add_field(name="💥 Faction Score", value=str(score), inline=True)

        embed.set_thumbnail(
            url="https://cdn-icons-png.flaticon.com/512/616/616408.png"
        )  # placeholder icon
        embed.set_footer(text="Fight for your faction with emoji power!")

        await ctx.send(embed=embed)

    @commands.command(name="factionleaderboard", aliases=["fl"])
    async def factionleaderboard(self, ctx):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()

            # Fetch war start date
            c.execute("SELECT started_at FROM war_state WHERE id = 1")
            war_row = c.fetchone()
            war_start = (
                datetime.fromisoformat(war_row[0]) if war_row and war_row[0] else None
            )

            # Get faction scores
            c.execute(
                """
                SELECT factions.id, factions.name, factions.symbol, factions.color, 
                       SUM(faction_scores.usage_count) as score
                FROM factions
                LEFT JOIN faction_scores ON factions.id = faction_scores.faction_id
                GROUP BY factions.id
                ORDER BY score DESC
            """
            )
            rows = c.fetchall()

            # Get faction sizes
            c.execute(
                """
                SELECT faction_id, COUNT(*) 
                FROM user_factions 
                GROUP BY faction_id
            """
            )
            member_counts = dict(c.fetchall())

        if not rows:
            await ctx.send("No faction scores yet!")
            return

        # Colors and medals
        medals = ["🥇", "🥈", "🥉"]
        embed_color = int(rows[0][3].replace("#", "0x"), 16)
        embed = discord.Embed(title="🌟 Faction Leaderboard", color=embed_color)

        for idx, (fid, name, symbol, color, score) in enumerate(rows):
            medal = medals[idx] if idx < len(medals) else "🏅"
            members = member_counts.get(fid, 0)
            embed.add_field(
                name=f"{medal} {symbol} {name}",
                value=f"**Score:** `{score or 0}`\n**Members:** `{members}`",
                inline=False,
            )

        if war_start:
            embed.set_footer(
                text=f"Emoji War started on {war_start.strftime('%Y-%m-%d')}"
            )
            embed.timestamp = war_start

        await ctx.send(embed=embed)

    @commands.command(name="startwar")
    async def startwar(self, ctx):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT started_at FROM war_state WHERE id = 1")
            row = c.fetchone()

            if row and row[0]:
                started_at = datetime.fromisoformat(row[0])
                delta = datetime.utcnow() - started_at
                if delta.days < 7:
                    await ctx.send(
                        f":warning: An emoji war is already ongoing! It started on `{started_at.date()}`."
                    )
                    return

            now = datetime.utcnow().isoformat()
            c.execute("UPDATE war_state SET started_at = ? WHERE id = 1", (now,))
            c.execute("DELETE FROM faction_scores")
            conn.commit()

        await ctx.send(
            ":crossed_swords: A new emoji war has begun! Use emojis to represent your faction!"
        )

    @tasks.loop(minutes=5)
    async def check_war_end(self):
        logger.debug("Checking if the emoji war should end.")
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT started_at FROM war_state WHERE id = 1")
            result = c.fetchone()
            if not result or not result[0]:
                return
            start_time = datetime.fromisoformat(result[0])
            if datetime.utcnow() - start_time >= timedelta(weeks=1):
                c.execute("SELECT * FROM faction_scores")
                scores = c.fetchall()
                for faction_id, emoji, count, _ in scores:
                    c.execute(
                        "INSERT INTO war_history (faction_id, emoji, usage_count, ended_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                        (faction_id, emoji, count),
                    )
                c.execute("DELETE FROM faction_scores")
                c.execute("UPDATE war_state SET started_at = NULL WHERE id = 1")
                conn.commit()

                channel = discord.utils.get(self.bot.get_all_channels(), name="general")
                if channel:
                    await channel.send(
                        ":trophy: The emoji war has ended! Scores have been archived."
                    )


async def setup(bot):
    await bot.add_cog(FactionCog(bot))
    logger.info("Faction Cog loaded successfully.")
