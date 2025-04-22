import discord
from discord.ext import commands, tasks
import sqlite3
import datetime

import logging

logger = logging.getLogger(__name__)

XP_DB = "xp_users.db"
XP_COOLDOWN_SECONDS = 1  # 1 minute cooldown between XP gains per user

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

    async def add_xp(self, user_id, amount, guild=None):
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
                            logger.warning(f"Member {user_id} not found in guild {guild.name}")
                            return

                    await self.check_and_assign_roles(member, new_level)

                    # OPTIONAL: send a public level up message!
                    if member.guild.system_channel:  # Check it exists
                        await member.guild.system_channel.send(
                            f"🎉 {member.mention} leveled up to **{new_level}** and became a **{self.get_title_for_level(new_level)}**!"
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
        await self.add_xp(message.author.id, 10, guild=message.guild)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        await self.add_xp(user.id, 5, guild=reaction.message.guild)

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

        embed = discord.Embed(
            title=f"{user.display_name}'s Adventurer Profile",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Level", value=str(level))
        embed.add_field(name="XP", value=f"{xp} / {next_level_xp}")
        embed.add_field(name="Title", value=title)
        embed.set_footer(text="Earn XP by chatting and reacting!")

        await ctx.send(embed=embed)
