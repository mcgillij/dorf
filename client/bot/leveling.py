import sqlite3
import datetime
import random
import asyncio

import logging
import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

from bot.constants import (
    XP_DB,
    XP_COOLDOWN_SECONDS,
    LEVEL_THRESHOLDS,
    LEVEL_ROLE_MAPPING,
)
from bot.config import CHAT_CHANNEL_ID


async def send_fancy_levelup(destination, user, level, new_title=None, next_title=None):
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

    if new_title:
        embed.add_field(
            name="New Title Unlocked!", value=f"🎖️ {new_title}", inline=False
        )

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

    @commands.command(name="reassign_all_roles")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def reassign_all_roles(self, ctx):
        await ctx.send(
            "Reassigning roles based on user levels. This may take a moment..."
        )

        success_count = 0
        fail_count = 0

        for member in ctx.guild.members:
            if member.bot:
                continue

            level = self.get_user_level(member.id)  # Replace this with your own method
            if level is None:
                continue

            try:
                await self.check_and_assign_roles(member, level)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to assign role for {member.display_name}: {e}")
                fail_count += 1

        await ctx.send(
            f"Finished! ✅ {success_count} users updated, ❌ {fail_count} failed."
        )

    def get_user_level(self, user_id: int) -> int | None:
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute("SELECT level FROM user_xp WHERE user_id = ?", (user_id,))
            level = c.fetchone()
            return level[0] if level else None

    async def get_user_stats(self, ctx, guild=None, channel=None):
        user_id = ctx.author.id
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()

            # Fetch current XP
            c.execute(
                "SELECT level, prestige FROM user_xp WHERE user_id = ?",
                (user_id,),
            )
            row = c.fetchone()

            if row:
                level, prestige = row
                user = ctx.author
                title = get_title_for_level(level)
                flair = get_prestige_flair(prestige)
                prestige_title = get_prestige_title(prestige)
                bold_prestige_title = (
                    f"{flair}***{prestige_title}*** " if prestige_title else ""
                )
                return f"**{user}** the level {level} ({bold_prestige_title} {title} {flair})"

    async def add_xp(self, user_id, amount, guild=None, channel=None):
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()

            # Fetch current XP
            c.execute(
                "SELECT xp, level, last_message_ts, prestige FROM user_xp WHERE user_id = ?",
                (user_id,),
            )
            row = c.fetchone()

            now_ts = get_current_timestamp()
            if row:
                xp, level, last_ts, prestige = row
                if last_ts and (now_ts - last_ts) < XP_COOLDOWN_SECONDS:
                    return
                # prestige bonus
                bonus_mult = 1 + (prestige * 0.10)
                amount = int(amount * bonus_mult)

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

                    channel = self.bot.get_channel(CHAT_CHANNEL_ID)
                    if not channel:
                        channel = member.guild.system_channel
                    old_title = get_title_for_level(old_level)
                    new_title = get_title_for_level(new_level)
                    # only show title if it changed
                    title_changed = old_title != new_title
                    await send_fancy_levelup(
                        channel,
                        member,
                        new_level,
                        new_title if title_changed else None,
                        next_title=get_title_for_level(new_level + 1),
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
        title = get_title_for_level(level)
        flair = get_prestige_flair(prestige)
        prestige_title = get_prestige_title(prestige)
        bold_prestige_title = (
            f"{flair}***{prestige_title}*** " if prestige_title else ""
        )
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
        embed.add_field(
            name="Title", value=f"{bold_prestige_title}**{title}**{flair}", inline=True
        )
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
            c.execute("SELECT user_id, xp, prestige FROM user_xp ORDER BY xp DESC")
            rows = c.fetchall()

        user_ids = [row[0] for row in rows]
        prestige_lookup = {row[0]: row[2] for row in rows}

        if user.id not in user_ids:
            await ctx.send(f"{user.display_name} hasn't earned any XP yet!")
            return

        rank = user_ids.index(user.id) + 1
        prestige = prestige_lookup.get(user.id, 0)
        flair = get_prestige_flair(prestige)

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        medal = medals.get(rank, "🎖️")

        await ctx.send(
            f"{medal} {user.display_name} {flair} is ranked **#{rank}** out of {len(user_ids)} adventurers!"
        )

    @commands.command(name="leaderboard", aliases=["lb"])
    async def leaderboard(self, ctx, limit: int = 10):
        limit = max(1, min(limit, 20))
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT user_id, xp, level, prestige FROM user_xp ORDER BY xp DESC LIMIT ?",
                (limit,),
            )
            rows = c.fetchall()

        if not rows:
            await ctx.send("No adventurers found on the leaderboard yet!")
            return

        medals = ["🥇", "🥈", "🥉"]
        description = ""
        for idx, (user_id, xp, level, prestige) in enumerate(rows, start=1):
            user = await self.bot.fetch_user(user_id)
            title = get_title_for_level(level)
            flair = get_prestige_flair(prestige)
            prestige_title = get_prestige_title(prestige)
            bold_prestige_title = (
                f"{flair}***{prestige_title}*** " if prestige_title else ""
            )
            medal = medals[idx - 1] if idx <= 3 else "🎖️"

            description += f"{medal} **{idx}. {user.display_name} ** — Level {level} ({bold_prestige_title}{title}{flair}) — {xp} XP\n"

        embed = discord.Embed(
            title="🏆 Server Leaderboard",
            description=description,
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Top adventurers based on total XP!")
        await ctx.send(embed=embed)

    @commands.command(name="prestige")
    async def prestige(self, ctx):
        """Allows a user to prestige if they meet the requirements."""
        user_id = ctx.author.id
        guild = ctx.guild
        member = guild.get_member(user_id)
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT xp, level, prestige FROM user_xp WHERE user_id = ?", (user_id,)
            )
            row = c.fetchone()

        if not row:
            await ctx.send(
                "You haven't started your journey yet! Keep chatting to earn XP."
            )
            return

        xp, level, prestige = row

        if level < 50:
            await ctx.send(
                f"You must be at least **Level 50** to prestige! (You are Level {level})"
            )
            return

        confirm_message = await ctx.send(
            f"⚡ {ctx.author.mention}, are you sure you want to **Prestige**?\n"
            f"✨ You will reset to **Level 1**, but gain a **+10% XP boost** permanently!\n"
            f"Type `yes` to confirm or react ✅ within 30 seconds."
        )

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.content.lower() == "yes"
            )

        def reaction_check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == confirm_message.id
                and str(reaction.emoji) == "✅"
            )

        try:
            await confirm_message.add_reaction("✅")
        except discord.Forbidden:
            pass  # if bot can't react, oh well

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(self.bot.wait_for("message", check=check)),
                asyncio.create_task(
                    self.bot.wait_for("reaction_add", check=reaction_check)
                ),
            ],
            timeout=30.0,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if not done:
            await ctx.send("❌ Prestige cancelled (timeout).")
            return

        # Actually prestige the user
        with sqlite3.connect(XP_DB) as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE user_xp 
                SET xp = 0, level = 1, prestige = prestige + 1
                WHERE user_id = ?
                """,
                (user_id,),
            )
            conn.commit()

        new_prestige = prestige + 1
        await self.check_and_assign_roles(member, 0)

        embed = discord.Embed(
            title="🌟 Prestige Achieved!",
            description=(
                f"🎉 {ctx.author.mention} has prestiged to **Prestige {new_prestige}**!\n\n"
                "You are reborn stronger and faster! 💥\n"
                f"XP Boost: **+{new_prestige * 10}%**"
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(
            url=(
                ctx.author.avatar.url
                if ctx.author.avatar
                else ctx.author.default_avatar.url
            )
        )
        embed.set_footer(text="The journey begins anew...")

        await ctx.send(embed=embed)


def get_title_for_level(level):
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


def get_prestige_flair(prestige):
    if prestige == 0:
        return ""
    elif prestige < 3:
        return "⭐" * prestige
    elif prestige < 6:
        return "🔥" * (prestige // 2)
    else:
        return "🌌"  # Ultra prestige


def get_prestige_title(prestige: int) -> str:
    prestige_titles = {
        0: None,
        1: "Ascendant",
        2: "Voidborn",
        3: "Eternal",
        4: "Mythic",
        5: "Celestial",
        6: "Abyssal",
        7: "Dragonheart",
        8: "Divine",
        9: "Legend",
        10: "Transcendent",
        11: "Sovereign",
        12: "Architect",
        13: "Immortal",
        14: "Chronomancer",
        15: "Apotheosis",
        16: "Godslayer",
    }
    if prestige > 16:
        return "Legendary"
    return prestige_titles.get(prestige, "Unknown")


def create_progress_bar(current, total, length=10, filled_emoji="🟦", empty_emoji="⬜"):
    progress = current / total
    filled_blocks = int(progress * length)
    empty_blocks = length - filled_blocks
    return filled_emoji * filled_blocks + empty_emoji * empty_blocks, int(
        progress * 100
    )
