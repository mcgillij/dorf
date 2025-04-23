import random
import asyncio
import lmstudio as lms
from lmstudio import Chat
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


class Adventure(commands.Cog):
    def __init__(self, bot):
        logger.info("ADVENTURE TIME!")
        self.bot = bot
        self.model = lms.llm()
        self.active_quests = {}

    @commands.command()
    async def adventure(self, ctx):
        if ctx.author.id in self.active_quests:
            await ctx.send("You're already on an adventure!")
            return

        self.active_quests[ctx.author.id] = True

        chat = Chat(
            "You are an adventuring bard guiding the user on a quest. "
            "The user will always respond with: Run away, Stealth, or Fight. "
            "Present challenges that fit these choices."
        )
        chat.add_user_message("I want to go on an adventure!")

        # First prompt
        prediction = self.model.respond(chat)
        message = await ctx.send(f"**Your quest begins!**\n\n{prediction}")

        # Add reaction options
        for emoji in ["🏃‍♂️", "🥷", "⚔️"]:
            await message.add_reaction(emoji)

        # Wait for player reaction
        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ["🏃‍♂️", "🥷", "⚔️"]
                and reaction.message.id == message.id
            )

        try:
            reaction, user = await self.bot.wait_for(
                "reaction_add", timeout=60.0, check=check
            )
        except asyncio.TimeoutError:
            await ctx.send("You hesitated too long... the opportunity vanished.")
            del self.active_quests[ctx.author.id]
            return

        # Map emoji to action
        choice_map = {"🏃‍♂️": "Run away", "🥷": "Stealth", "⚔️": "Fight"}
        choice = choice_map[str(reaction.emoji)]

        await ctx.send(f"You chose to **{choice}**! Rolling for success... 🎲")

        # Determine success/failure chances
        success_chance = {
            "Run away": 0.8,  # 80%
            "Stealth": 0.65,  # 65%
            "Fight": 0.5,  # 50%
        }

        # Roll
        roll = random.random()
        passed = roll < success_chance[choice]

        if passed:
            chat.add_user_message(f"I choose to {choice} and I succeed!")
            outcome_text = "**Success!** 🎉 You overcame the challenge!"
            xp_earned = random.randint(40, 60)
        else:
            chat.add_user_message(f"I choose to {choice} but I fail...")
            outcome_text = "**Failure!** 😢 You were bested by the challenge."
            xp_earned = random.randint(10, 20)

        # LLM continues the story based on outcome
        continuation = self.model.respond(chat)

        await ctx.send(f"{outcome_text}\n\n{continuation}")

        # Award XP
        leveling_cog = self.bot.get_cog("Leveling")
        if leveling_cog:
            await leveling_cog.add_xp(ctx.author, xp_earned)

        await ctx.send(f"You gained **{xp_earned} XP** for your adventure!")

        # Cleanup
        del self.active_quests[ctx.author.id]


async def setup(bot):
    await bot.add_cog(Adventure(bot))
