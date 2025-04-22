import discord
from discord.ext import commands, tasks
import sqlite3
import datetime
import random

import logging

logger = logging.getLogger(__name__)

XP_DB = "xp_users.db"
XP_COOLDOWN_SECONDS = 10  # 1 minute cooldown between XP gains per user

LEVEL_THRESHOLDS = lambda lvl: 5 * (lvl**2) + 50 * lvl + 100

LEVEL_ROLE_MAPPING = {
    0: 1364048765727801344,  # Wanderer
    5: 1364042278930219120,  # Noob
    10: 1364042608757837965,  # Scrub
    15: 1364045146521600041,  # Squire
    20: 1364042802643865621,  # Knight
    25: 1364043033036722276,  # Spellblade
    30: 1364045512659046410,  # Berzerker
    35: 1364045794180726825,  # Paladin
    40: 1364043151475605655,  # Archmage
    45: 1364043083590795397,  # Dragonlord
    50: 1364043193003544690,  # Einherjar
}


async def send_fancy_levelup(destination, user, level, title, next_title=None):
    phrases = [
        "has ascended!",
        "leveled up!",
        "evolved!",
        "unlocked a new path!",
        "awakened!",
        "powered up!",
        "achieved greatness!",
        "grew stronger!",
    ]
    random_phrase = random.choice(phrases)

    embed = discord.Embed(
        title=f"🌟 {user.display_name} {random_phrase}",
        description=f"🆙 Reached **Level {level}**!",
        color=discord.Color.from_rgb(255, 215, 0),  # Gold color
    )
    embed.add_field(name="New Title", value=f"🎖️ {title}", inline=False)

    if next_title:
        embed.add_field(
            name="Next Title",
            value=f"🔜 {next_title} at Level {level + 1}",
            inline=False,
        )

    if user.avatar:
        embed.set_thumbnail(url=user.avatar.url)
    else:
        embed.set_thumbnail(url=user.default_avatar.url)

    embed.set_footer(text="Keep chatting to grow stronger! 🚀")

    await destination.send(embed=embed)


def get_current_timestamp():
    return datetime.datetime.utcnow().timestamp()


async def setup(bot):
    await bot.add_cog(Leveling(bot))
    logger.info("Leveling Cog loaded successfully.")


class Leveling(commands.Cog):
    def __init__(self, bot):
        logger.info("Initializing Leveling Cog")
        self.bot = bot
        self.init_db()

    def init_db(self):
        logger.info("Initializing XP database")
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS user_xp (
                    user_id INTEGER PRIMARY KEY,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    prestige INTEGER DEFAULT 0,
                    last_message_ts REAL
                )
            """
            )
            conn.commit()

    def get_title_for_level(self, level):
        titles = [
            (0, "Wanderer"),
            (5, "Noob"),
            (10, "Scrub"),
            (15, "Squire"),
            (20, "Knight"),
            (25, "Spellblade"),
            (30, "Berzerker"),
            (35, "Paladin"),
            (40, "Archmage"),
            (45, "Dragonlord"),
            (50, "Einherjar"),
        ]
        for lvl, title in reversed(titles):
            if level >= lvl:
                return title
        return "Wanderer"

    async def check_and_assign_roles(self, member, new_level):
        roles_to_assign = []

        for level_req, role_id in LEVEL_ROLE_MAPPING.items():
            if new_level >= level_req:
                role = member.guild.get_role(role_id)
                if role:
                    roles_to_assign.append(role)

        if roles_to_assign:
            # Optionally: remove lower level roles first
            current_roles = set(member.roles)
            valid_role_ids = set(LEVEL_ROLE_MAPPING.values())
            roles_to_remove = [
                role for role in current_roles if role.id in valid_role_ids
            ]

            try:
                await member.remove_roles(*roles_to_remove)
                await member.add_roles(roles_to_assign[-1])  # Assign highest one
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error(f"Failed to assign roles for {member.display_name}: {e}")

    async def add_xp(self, user_id, amount, guild=None, channel=None):
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()

            # Fetch current XP
            c.execute(
                "SELECT xp, level, last_message_ts FROM user_xp WHERE user_id = ?",
                (user_id,),
            )
            row = c.fetchone()

            now_ts = get_current_timestamp()
            if row:
                xp, level, last_ts = row
                if last_ts and (now_ts - last_ts) < XP_COOLDOWN_SECONDS:
                    return

                xp += amount
                old_level = level
                new_level = level
                while xp >= LEVEL_THRESHOLDS(new_level):
                    new_level += 1

                c.execute(
                    """
                    UPDATE user_xp SET xp = ?, level = ?, last_message_ts = ?
                    WHERE user_id = ?
                """,
                    (xp, new_level, now_ts, user_id),
                )

                conn.commit()
                if new_level > old_level and guild:
                    member = guild.get_member(user_id)
                    if member is None:
                        try:
                            member = await guild.fetch_member(user_id)
                        except discord.NotFound:
                            logger.warning(
                                f"Member {user_id} not found in guild {guild.name}"
                            )
                            return

                    await self.check_and_assign_roles(member, new_level)

                    # OPTIONAL: send a public level up message!
                    if member.guild.system_channel:  # Check it exists
                        # f"🎉 {member.mention} leveled up to **{new_level}** and became a **{self.get_title_for_level(new_level)}**!"
                        await send_fancy_levelup(
                            member.guild.system_channel,
                            member,
                            new_level,
                            self.get_title_for_level(old_level),
                            self.get_title_for_level(new_level),
                        )

            else:
                xp = amount
                level = 1
                while xp >= LEVEL_THRESHOLDS(level):
                    level += 1

                c.execute(
                    """
                    INSERT INTO user_xp (user_id, xp, level, last_message_ts)
                    VALUES (?, ?, ?, ?)
                """,
                    (user_id, xp, level, now_ts),
                )

                conn.commit()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        await self.add_xp(
            message.author.id, 10, guild=message.guild, channel=message.channel
        )

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        await self.add_xp(
            user.id, 5, guild=reaction.message.guild, channel=reaction.message.channel
        )

    @commands.command(name="profile")
    async def profile(self, ctx, user: discord.User = None):
        user = user or ctx.author
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT xp, level, prestige FROM user_xp WHERE user_id = ?", (user.id,)
            )
            row = c.fetchone()

        if not row:
            await ctx.send(f"{user.display_name} hasn't earned any XP yet!")
            return

        xp, level, prestige = row
        next_level_xp = LEVEL_THRESHOLDS(level)
        title = self.get_title_for_level(level)

        xp_bar, percent = create_progress_bar(xp, next_level_xp)

        color = (
            discord.Color.gold()
            if level >= 40
            else discord.Color.blurple() if level >= 20 else discord.Color.greyple()
        )

        embed = discord.Embed(
            title=f"✨ {user.display_name}'s Adventurer Profile",
            color=color,
        )
        embed.add_field(name="Level", value=f"{level}", inline=True)
        embed.add_field(name="XP", value=f"{xp} / {next_level_xp}", inline=True)
        embed.add_field(name="Progress", value=f"{xp_bar} {percent}%", inline=False)
        embed.add_field(name="Title", value=f"**{title}**", inline=True)
        embed.add_field(name="Prestige", value=f"{prestige}", inline=True)
        embed.add_field(
            name="Next Level In", value=f"{next_level_xp - xp} XP", inline=True
        )
        embed.set_thumbnail(
            url=user.avatar.url if user.avatar else user.default_avatar.url
        )
        embed.set_footer(text="Earn XP by chatting and reacting!")

        await ctx.send(embed=embed)

    @commands.command(name="rank")
    async def rank(self, ctx, user: discord.User = None):
        user = user or ctx.author
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, xp FROM user_xp ORDER BY xp DESC")
            rows = c.fetchall()

        user_ids = [row[0] for row in rows]
        if user.id not in user_ids:
            await ctx.send(f"{user.display_name} hasn't earned any XP yet!")
            return

        rank = user_ids.index(user.id) + 1

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        medal = medals.get(rank, "🎖️")

        await ctx.send(
            f"{medal} {user.display_name} is ranked **#{rank}** out of {len(user_ids)} adventurers!"
        )

    @commands.command(name="leaderboard", aliases=["lb"])
    async def leaderboard(self, ctx, limit: int = 10):
        limit = max(1, min(limit, 20))  # Don't allow huge requests
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT user_id, xp, level FROM user_xp ORDER BY xp DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()

        if not rows:
            await ctx.send("No adventurers found on the leaderboard yet!")
            return

        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for idx, (user_id, xp, level) in enumerate(rows, start=1):
            user = await self.bot.fetch_user(user_id)
            title = self.get_title_for_level(level)
            medal = medals[idx - 1] if idx <= 3 else "🎖️"

            description += f"{medal} **{idx}. {user.display_name}** — Level {level} ({title}) — {xp} XP\n"

        embed = discord.Embed(
            title="🏆 Server Leaderboard",
            description=description,
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Top adventurers based on total XP!")
        await ctx.send(embed=embed)


def create_progress_bar(current, total, length=10, filled_emoji="🟦", empty_emoji="⬜"):
    progress = current / total
    filled_blocks = int(progress * length)
    empty_blocks = length - filled_blocks
    return filled_emoji * filled_blocks + empty_emoji * empty_blocks, int(
        progress * 100
    )
