import logging
import sqlite3

from discord.ext import commands
from bot.constants import MACRO_DB
from bot.db import open_db

logger = logging.getLogger(__name__)


class MacroCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = open_db(MACRO_DB)
        self._create_table()

    def _create_table(self):
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS macros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    name TEXT,
                    response TEXT,
                    created_by TEXT,
                    UNIQUE(guild_id, name)
                )""")

    @commands.command()
    async def addmacro(self, ctx, name: str, *, response: str):
        """Add a macro, format <name>:str <actualmacro>:str"""
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO macros (guild_id, name, response, created_by) VALUES (?, ?, ?, ?)",
                    (ctx.guild.id, name.lower(), response, str(ctx.author)),
                )
            await ctx.send(f"Macro `{name}` added! ✅")
        except sqlite3.IntegrityError:
            await ctx.send("A macro with that name already exists.")

    @commands.command()
    async def delmacro(self, ctx, name: str):
        """Delete's a macro, given the name"""
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        cur = self.db.execute(
            "SELECT created_by FROM macros WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name.lower()),
        )
        row = cur.fetchone()
        if not row:
            await ctx.send("No such macro found.")
            return
        is_creator = row[0] == str(ctx.author)
        is_mod = ctx.author.guild_permissions.manage_messages
        if not (is_creator or is_mod):
            await ctx.send(
                "You can only delete macros you created (or have Manage Messages)."
            )
            return
        with self.db:
            cur = self.db.execute(
                "DELETE FROM macros WHERE guild_id = ? AND name = ?",
                (ctx.guild.id, name.lower()),
            )
        await ctx.send(f"Macro `{name}` deleted.")

    @commands.command()
    async def listmacros(self, ctx):
        """show the list of macros"""
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return
        cur = self.db.execute(
            "SELECT name FROM macros WHERE guild_id = ? ORDER BY name",
            (ctx.guild.id,),
        )
        macros = [row[0] for row in cur.fetchall()]
        if macros:
            await ctx.send("Available macros:\n" + ", ".join(f"`{m}`" for m in macros))
        else:
            await ctx.send("No macros set up in this server.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return  # Ignore DMs
        if message.author.bot:
            return

        if not message.content.startswith("!"):
            return

        parts = message.content[1:].split()
        if not parts:
            return
        command_name = parts[0].lower()

        # Real commands take precedence over macros (prevents double responses)
        if self.bot.get_command(command_name) is not None:
            return

        cur = self.db.execute(
            "SELECT response FROM macros WHERE guild_id = ? AND name = ?",
            (message.guild.id, command_name),
        )
        result = cur.fetchone()
        if result:
            await message.channel.send(result[0])
            return


async def setup(bot):
    logger.info("Loading MacroCog...")
    await bot.add_cog(MacroCog(bot))
