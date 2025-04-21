import discord


class PollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.votes_yes = 0
        self.votes_no = 0

    @discord.ui.button(label="👍 Yes", style=discord.ButtonStyle.success)
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.votes_yes += 1
        await interaction.response.send_message(
            f"You voted YES! (Total yes votes: {self.votes_yes})", ephemeral=True
        )

    @discord.ui.button(label="👎 No", style=discord.ButtonStyle.danger)
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.votes_no += 1
        await interaction.response.send_message(
            f"You voted NO! (Total no votes: {self.votes_no})", ephemeral=True
        )
