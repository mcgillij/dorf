import logging
import sqlite3

from discord.ext import commands
from bot.constants import MACRO_DB

logger = logging.getLogger(__name__)


class MacroCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = sqlite3.connect(MACRO_DB)
        self._create_table()

    def _create_table(self):
        with self.db:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS macros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    name TEXT,
                    response TEXT,
                    created_by TEXT,
                    UNIQUE(guild_id, name)
                )"""
            )

    @commands.command()
    async def addmacro(self, ctx, name: str, *, response: str):
        """Create a new macro."""
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
        """Delete a macro."""
        with self.db:
            cur = self.db.execute(
                "DELETE FROM macros WHERE guild_id = ? AND name = ?",
                (ctx.guild.id, name.lower()),
            )
        if cur.rowcount:
            await ctx.send(f"Macro `{name}` deleted.")
        else:
            await ctx.send("No such macro found.")

    @commands.command()
    async def listmacros(self, ctx):
        """List all macros."""
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
        if message.author.bot:
            return

        if not message.content.startswith("!"):
            return

        command_name = message.content[1:].split()[0].lower()

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
