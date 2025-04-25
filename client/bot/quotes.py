import sqlite3
import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


# Connect to your SQLite DB
conn = sqlite3.connect("quotes.db")
c = conn.cursor()

# Ensure quotes table exists
c.execute(
    """
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT,
    quote_text TEXT NOT NULL,
    added_by TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT,
    pinned BOOLEAN DEFAULT 0
)
"""
)
conn.commit()


class QuoteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.quote_emoji = "🏆"

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        logger.info(f"Reaction added by {user}: {reaction.emoji}")
        if user.bot:
            return  # Ignore bot reactions

        if str(reaction.emoji) != self.quote_emoji:
            return

        message = reaction.message

        # Avoid re-quoting already quoted messages if needed
        c.execute(
            "SELECT 1 FROM quotes WHERE quote_text = ? AND author = ?",
            (message.content, str(message.author)),
        )
        if c.fetchone():
            return  # Already quoted

        # Save quote to the DB
        c.execute(
            "INSERT INTO quotes (author, quote_text, added_by, source) VALUES (?, ?, ?, ?)",
            (str(message.author), message.content, str(user), message.jump_url),
        )
        conn.commit()

        await message.channel.send(
            f"🏆 Quote saved from {message.author.display_name}!"
        )

    @commands.command(name="quote", aliases=["q"])
    async def quote(self, ctx, arg=None):
        if arg is None:
            # Fetch random quote
            c.execute(
                "SELECT id, author, quote_text, source FROM quotes ORDER BY RANDOM() LIMIT 1"
            )
        elif arg.isdigit():
            c.execute(
                "SELECT id, author, quote_text, source FROM quotes WHERE id = ?", (arg,)
            )
        else:
            # Search quotes by keyword
            c.execute(
                "SELECT id, author, quote_text FROM quotes WHERE quote_text LIKE ?",
                (f"%{arg}%",),
            )

        result = c.fetchone()
        if result:
            id, author, quote_text, source = result
            await ctx.send(
                f"**#{id}** {author+': ' if author else ''}{quote_text} - {source}"
            )
        else:
            await ctx.send("Quote not found!")

    @commands.command(name="addquote", aliases=["aq"])
    async def addquote(self, ctx, *, text):
        # Optional: try to detect if user wants to specify the author manually.
        if "|" in text:
            author, quote = text.split("|", 1)
            author = author.strip()
            quote = quote.strip()
        else:
            author = None
            quote = text.strip()

        c.execute(
            "INSERT INTO quotes (author, quote_text, added_by) VALUES (?, ?, ?)",
            (author, quote, str(ctx.author)),
        )
        conn.commit()
        await ctx.send("Quote added! ✅")

    @commands.command(name="listquotes", aliases=["lq"])
    async def listquotes(self, ctx, page: int = 1):
        per_page = 5
        offset = (page - 1) * per_page
        c.execute(
            "SELECT id, author, quote_text FROM quotes ORDER BY id LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        quotes = c.fetchall()
        if not quotes:
            await ctx.send("No quotes found on that page.")
            return
        text = "\n".join(
            [
                f"**#{id}** {author+': ' if author else ''}{quote}"
                for id, author, quote in quotes
            ]
        )
        await ctx.send(f"**Quotes (Page {page}):**\n{text}")

    @commands.command(name="deletequote", aliases=["dq"])
    @commands.has_permissions(manage_messages=True)
    async def deletequote(self, ctx, id: int):
        c.execute("DELETE FROM quotes WHERE id = ?", (id,))
        conn.commit()
        await ctx.send(f"Quote #{id} deleted! 🗑️")

    @commands.command(name="searchquote", aliases=["sq"])
    async def searchquote(self, ctx, *, keyword):
        c.execute(
            "SELECT id, author, quote_text FROM quotes WHERE quote_text LIKE ?",
            (f"%{keyword}%",),
        )
        results = c.fetchall()
        if not results:
            await ctx.send("No quotes matching that keyword.")
            return
        text = "\n".join(
            [
                f"**#{id}** {author+': ' if author else ''}{quote}"
                for id, author, quote in results
            ]
        )
        await ctx.send(f"**Search Results:**\n{text[:2000]}")


async def setup(bot):
    await bot.add_cog(QuoteCog(bot))
    logger.info("REACTION Cog loaded successfully.")
