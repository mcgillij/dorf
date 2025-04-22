import discord

active_polls = {}  # Dictionary to keep track of active polls


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
