import sqlite3
import random
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from bot.constants import FACTION_DB, DEFAULT_FACTIONS
from bot.config import CHAT_CHANNEL_ID
from bot.emoji import extract_emojis

logger = logging.getLogger(__name__)


class FactionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.init_db()
        self.check_war_end.start()
        self.check_war_warnings.start()

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

            # Add warning columns if they don't exist yet (safe even if they already exist)
            c.execute("PRAGMA table_info(war_state)")
            columns = [row[1] for row in c.fetchall()]
            if "warning_24h_sent" not in columns:
                c.execute(
                    "ALTER TABLE war_state ADD COLUMN warning_24h_sent INTEGER DEFAULT 0"
                )
            if "warning_12h_sent" not in columns:
                c.execute(
                    "ALTER TABLE war_state ADD COLUMN warning_12h_sent INTEGER DEFAULT 0"
                )
            if "warning_1h_sent" not in columns:
                c.execute(
                    "ALTER TABLE war_state ADD COLUMN warning_1h_sent INTEGER DEFAULT 0"
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

    async def cog_unload(self):
        self.check_war_warnings.cancel()

    @tasks.loop(minutes=5)
    async def check_war_warnings(self):
        WAR_DURATION_DAYS = 7
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT started_at, warning_24h_sent, warning_12h_sent, warning_1h_sent FROM war_state WHERE id = 1"
            )
            row = c.fetchone()

            if not row or not row[0]:
                return  # No war active

            war_start = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
            war_end = war_start + timedelta(days=WAR_DURATION_DAYS)
            now = datetime.now(timezone.utc)
            time_left = war_end - now

            warning_24h_sent, warning_12h_sent, warning_1h_sent = row[1:]

            channel = self.bot.get_channel(CHAT_CHANNEL_ID)
            if channel is None:
                return  # channel doesn't exist, fail silently

            updates = {}

            if time_left.total_seconds() <= 86400 and not warning_24h_sent:
                await channel.send(
                    "⚔️ **24 hours remaining in the War!** Rally your forces and make every emoji count!"
                )
                updates["warning_24h_sent"] = 1

            if time_left.total_seconds() <= 43200 and not warning_12h_sent:
                await channel.send(
                    "⏳ **12 hours left!** The final stretch is here — unleash your emoji power!"
                )
                updates["warning_12h_sent"] = 1

            if time_left.total_seconds() <= 3600 and not warning_1h_sent:
                await channel.send(
                    "🔥 **Only 1 hour left!!** Everything you do now could change the outcome!"
                )
                updates["warning_1h_sent"] = 1

            if updates:
                # Save which warnings fired
                set_clause = ", ".join([f"{key} = ?" for key in updates])
                params = list(updates.values())
                params.append(1)  # WHERE id = 1
                c.execute(f"UPDATE war_state SET {set_clause} WHERE id = ?", params)
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
                datetime.fromisoformat(joined_at_row[0]).replace(tzinfo=timezone.utc)
                if joined_at_row
                else None
            )

            # Faction members (fetch all user_ids instead of just count)
            c.execute(
                "SELECT user_id FROM user_factions WHERE faction_id = ?", (faction_id,)
            )
            user_ids = [row[0] for row in c.fetchall()]

            # Faction score
            c.execute(
                "SELECT SUM(usage_count) FROM faction_scores WHERE faction_id = ?",
                (faction_id,),
            )
            score = c.fetchone()[0] or 0

        # Fetch usernames
        members = []
        for uid in user_ids:
            member = ctx.guild.get_member(uid)
            if member:
                members.append(member.display_name)
            else:
                # fallback if user isn't in guild anymore
                members.append(f"User ID {uid}")

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
        embed.add_field(name="🧑‍🤝‍🧑 Members", value=str(len(user_ids)), inline=True)
        embed.add_field(name="💥 Faction Score", value=str(score), inline=True)

        # New: add a field listing the members
        member_list = ", ".join(members)
        if len(member_list) > 1024:
            member_list = member_list[:1020] + "..."  # Discord field limit

        embed.add_field(name="👥 Member List", value=member_list, inline=False)

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
                datetime.fromisoformat(war_row[0]).replace(tzinfo=timezone.utc)
                if war_row and war_row[0]
                else None
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

            # Get faction members
            c.execute(
                """
                SELECT user_id, faction_id 
                FROM user_factions
            """
            )
            all_members = c.fetchall()
            member_map = {}
            for user_id, faction_id in all_members:
                member_map.setdefault(faction_id, []).append(user_id)

        if not rows:
            await ctx.send("No faction scores yet!")
            return

        medals = ["🥇", "🥈", "🥉"]
        embed_color = int(rows[0][3].replace("#", "0x"), 16)
        embed = discord.Embed(title="🌟 Faction Leaderboard", color=embed_color)

        for idx, (fid, name, symbol, color, score) in enumerate(rows):
            medal = medals[idx] if idx < len(medals) else "🏅"
            member_ids = member_map.get(fid, [])

            members = []
            for member_id in member_ids:
                member = self.bot.get_user(member_id) or await self.bot.fetch_user(
                    member_id
                )
                members.append(
                    member.display_name if member else f"User ID {member_id}"
                )

            member_list = ", ".join(members) if members else "No members"
            if len(member_list) > 1024:
                member_list = member_list[:1020] + "..."

            embed.add_field(
                name=f"{medal} {symbol} {name}",
                value=f"**Score:** `{score or 0}`\n**Members ({len(members)}):** {member_list}",
                inline=False,
            )

        if war_start:
            embed.set_footer(
                text=f"Emoji War started on {war_start.strftime('%Y-%m-%d')}"
            )
            embed.timestamp = war_start

        await ctx.send(embed=embed)

    @commands.command(name="warstatus", aliases=["ws", "war_status"])
    async def war_status(self, ctx):
        WAR_DURATION_DAYS = 7
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()

            # War start date
            c.execute("SELECT started_at FROM war_state WHERE id = 1")
            war_row = c.fetchone()
            if not war_row or not war_row[0]:
                await ctx.send("No war is currently active!")
                return
            war_start = datetime.fromisoformat(war_row[0]).replace(tzinfo=timezone.utc)
            war_end = war_start + timedelta(days=WAR_DURATION_DAYS)
            time_remaining = war_end - datetime.now(timezone.utc)

            # Total emoji usages
            c.execute("SELECT SUM(usage_count) FROM faction_scores")
            total_usage = c.fetchone()[0] or 0

            # Faction scores
            c.execute(
                """
                SELECT factions.name, factions.symbol, SUM(faction_scores.usage_count) as score
                FROM factions
                LEFT JOIN faction_scores ON factions.id = faction_scores.faction_id
                GROUP BY factions.id
                ORDER BY score DESC
            """
            )
            faction_rows = c.fetchall()

        if not faction_rows:
            await ctx.send("No faction scores yet!")
            return

        leader_name, leader_symbol, leader_score = faction_rows[0]

        embed = discord.Embed(
            title="⚔️ War Status",
            description="The battle rages on! Here's the current situation:",
            color=0xFF5555,
        )

        embed.add_field(
            name="📅 War Started", value=war_start.strftime("%Y-%m-%d"), inline=True
        )
        embed.add_field(
            name="🧮 Total Emoji Uses", value=f"{total_usage:,}", inline=True
        )
        embed.add_field(
            name="🏆 Leading Faction",
            value=f"{leader_symbol} **{leader_name}** with **{leader_score:,}** points",
            inline=False,
        )

        # Top 3 factions
        top_factions = ""
        medals = ["🥇", "🥈", "🥉"]
        for idx, (name, symbol, score) in enumerate(faction_rows[:3]):
            medal = medals[idx] if idx < len(medals) else ""
            top_factions += f"{medal} {symbol} **{name}** - {score or 0:,} points\n"
        embed.add_field(name="🏅 Top Factions", value=top_factions, inline=False)

        # Time remaining
        if time_remaining.total_seconds() > 0:
            days, remainder = divmod(int(time_remaining.total_seconds()), 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, _ = divmod(remainder, 60)
            time_left_str = f"{days}d {hours}h {minutes}m"
        else:
            time_left_str = "The war has ended!"

        embed.add_field(name="⏳ Time Remaining", value=time_left_str, inline=False)

        embed.set_footer(text="Fight for your faction with emoji power!")
        embed.timestamp = datetime.now(timezone.utc)

        await ctx.send(embed=embed)

    @commands.command(name="startwar")
    async def startwar(self, ctx):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT started_at FROM war_state WHERE id = 1")
            row = c.fetchone()

            if row and row[0]:
                started_at = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - started_at
                if delta.days < 7:
                    await ctx.send(
                        f":warning: An emoji war is already ongoing! It started on `{started_at.date()}`."
                    )
                    return

            now = datetime.now(timezone.utc).isoformat()
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
            start_time = datetime.fromisoformat(result[0]).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - start_time >= timedelta(weeks=1):
                # Fetch faction scores
                c.execute(
                    """
                    SELECT factions.name, factions.symbol, SUM(faction_scores.usage_count) as score
                    FROM factions
                    LEFT JOIN faction_scores ON factions.id = faction_scores.faction_id
                    GROUP BY factions.id
                    ORDER BY score DESC
                    """
                )
                scores = c.fetchall()

                # Prepare the announcement message
                if scores:
                    winner_name, winner_symbol, winner_score = scores[0]
                    message = (
                        f":trophy: The emoji war has ended!\n\n"
                        f"🏆 **Winner:** {winner_symbol} **{winner_name}** with **{winner_score:,}** points!\n\n"
                        f"📊 **Final Standings:**\n"
                    )
                    medals = ["🥇", "🥈", "🥉"]
                    for idx, (name, symbol, score) in enumerate(scores[:3]):
                        medal = medals[idx] if idx < len(medals) else ""
                        message += (
                            f"{medal} {symbol} **{name}** - {score or 0:,} points\n"
                        )
                else:
                    message = (
                        ":trophy: The emoji war has ended, but no scores were recorded!"
                    )

                # Send the announcement
                channel = discord.utils.get(
                    self.bot.get_all_channels(), name="bot-spam"
                )
                if channel:
                    await channel.send(message)

                # Archive scores and reset war state
                for faction_id, emoji, count, _ in scores:
                    c.execute(
                        "INSERT INTO war_history (faction_id, emoji, usage_count, ended_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                        (faction_id, emoji, count),
                    )
                c.execute("DELETE FROM faction_scores")
                c.execute("UPDATE war_state SET started_at = NULL WHERE id = 1")
                conn.commit()
                await self.reset_factions()

    async def reset_factions(self):
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            # Clear faction scores
            c.execute("DELETE FROM faction_scores")
            # Clear factions
            c.execute("DELETE FROM factions")
            conn.commit()

        return "All factions and scores have been reset. Ready for the next war!"

    @commands.command(name="war_history")
    async def show_war_history(self, ctx):
        """Displays the results of past wars from the war_history table."""
        with sqlite3.connect(FACTION_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT factions.name, factions.symbol, COALESCE(SUM(war_history.usage_count), 0) as score
                FROM factions
                LEFT JOIN war_history ON factions.id = war_history.faction_id
                GROUP BY factions.id
                ORDER BY score DESC
                """
            )
            results = c.fetchall()

        if not results:
            await ctx.send("No war history available.")
            return

        report = ":scroll: **War History**\n\n"
        for name, symbol, score in results:
            report += f"**{name}** ({symbol}):\n"
            report += f"  Total Score - {score:,} points\n\n"

        await ctx.send(report)


async def setup(bot):
    await bot.add_cog(FactionCog(bot))
    logger.info("Faction Cog loaded successfully.")
