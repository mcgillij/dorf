from discord.ext import commands
import sqlite3


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


@commands.command()
async def quote(ctx, arg=None):
    if arg is None:
        # Fetch random quote
        c.execute("SELECT id, author, quote_text FROM quotes ORDER BY RANDOM() LIMIT 1")
    elif arg.isdigit():
        c.execute("SELECT id, author, quote_text FROM quotes WHERE id = ?", (arg,))
    else:
        # Search quotes by keyword
        c.execute(
            "SELECT id, author, quote_text FROM quotes WHERE quote_text LIKE ?",
            (f"%{arg}%",),
        )

    result = c.fetchone()
    if result:
        id, author, quote_text = result
        await ctx.send(f"**#{id}** {author+': ' if author else ''}{quote_text}")
    else:
        await ctx.send("Quote not found!")


@commands.command()
async def addquote(ctx, *, text):
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


@commands.command()
async def listquotes(ctx, page: int = 1):
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


@commands.command()
@commands.has_permissions(manage_messages=True)
async def deletequote(ctx, id: int):
    c.execute("DELETE FROM quotes WHERE id = ?", (id,))
    conn.commit()
    await ctx.send(f"Quote #{id} deleted! 🗑️")


@commands.command()
async def searchquote(ctx, *, keyword):
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
