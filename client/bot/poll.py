import logging
import asyncio
import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

active_polls = {}  # Dictionary to keep track of active polls


class PollCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="poll", aliases=["p"])
    async def poll(self, ctx, *, question: str):
        """Create a poll that automatically ends after 5 minutes."""
        poll_id = str(ctx.message.id)
        active_polls[poll_id] = {
            "question": question,
            "yes": 0,
            "no": 0,
            "channel_id": ctx.channel.id,
            "message_id": None,
        }

        embed = discord.Embed(
            title="📊 New Poll!",
            description=f"{question}\n\n*Poll ends in 5 minutes!*",
            color=discord.Color.blue(),
        )
        view = PollView(poll_id)
        message = await ctx.send(embed=embed, view=view)

        active_polls[poll_id]["message_id"] = message.id

        # Start background task
        ctx.bot.loop.create_task(close_poll_after_delay(ctx, poll_id, view))


async def close_poll_after_delay(ctx, poll_id, view):
    await asyncio.sleep(300)  # 5 minutes
    view.close_poll()

    poll = active_polls.get(poll_id)
    if not poll:
        return

    channel = ctx.bot.get_channel(poll["channel_id"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(poll["message_id"])
    except discord.NotFound:
        return

    # Update the embed to show "Poll Ended"
    embed = discord.Embed(
        title="📋 Poll Ended!",
        description=f"**{poll['question']}**\n\n👍 Yes: {poll['yes']}\n👎 No: {poll['no']}",
        color=discord.Color.gold(),
    )
    await message.edit(embed=embed, view=view)

    # Clean up
    active_polls.pop(poll_id, None)


async def setup(bot):
    await bot.add_cog(PollCog(bot))
    logger.info("POLL Cog loaded successfully.")


class PollView(discord.ui.View):
    def __init__(self, poll_id):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.is_closed = False
        self.votes_yes = 0
        self.votes_no = 0
        self.voters = set()  # Set of user IDs who already voted

    @discord.ui.button(
        label="👍 Yes", style=discord.ButtonStyle.success, custom_id="poll_yes"
    )
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.is_closed:
            await interaction.response.send_message(
                "⏳ This poll has ended. You can't vote anymore!", ephemeral=True
            )
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message(
                "❌ You already voted!", ephemeral=True
            )
            return

        self.voters.add(interaction.user.id)
        self.votes_yes += 1
        active_polls[self.poll_id]["yes"] = self.votes_yes
        await interaction.response.send_message("✅ You voted YES!", ephemeral=True)

    @discord.ui.button(
        label="👎 No", style=discord.ButtonStyle.danger, custom_id="poll_no"
    )
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.is_closed:
            await interaction.response.send_message(
                "⏳ This poll has ended. You can't vote anymore!", ephemeral=True
            )
            return
        if interaction.user.id in self.voters:
            await interaction.response.send_message(
                "❌ You already voted!", ephemeral=True
            )
            return

        self.voters.add(interaction.user.id)
        self.votes_no += 1
        active_polls[self.poll_id]["no"] = self.votes_no
        await interaction.response.send_message("✅ You voted NO!", ephemeral=True)

    def close_poll(self):
        self.is_closed = True
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True  # Disable all buttons
