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

    async def send_adventure_message(self, ctx, content, add_reactions=True):
        """Helper to send a message and optionally add reaction options."""
        message = await ctx.send(content)
        if add_reactions:
            for emoji in ["🏃‍♂️", "🥷", "⚔️"]:
                await message.add_reaction(emoji)
        return message

    @commands.command()
    async def adventure(self, ctx):
        if ctx.author.id in self.active_quests:
            await ctx.send("You're already on an adventure!")
            return

        self.active_quests[ctx.author.id] = {"wins": 0, "losses": 0}

        chat = Chat(
            "You are an adventuring bard guiding the user on a quest. "
            "The user will always respond with: Run away, Stealth, or Fight. "
            "Present challenges that fit these choices."
        )
        chat.add_user_message("I want to go on an adventure!")

        # First prompt
        prediction = self.model.respond(chat)
        message = await self.send_adventure_message(
            ctx, f"**Your quest begins!**\n\n{prediction}"
        )

        while True:
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

            # Update history
            if passed:
                chat.add_user_message(f"I choose to {choice} and I succeed!")
                outcome_text = "**Success!** 🎉 You overcame the challenge!"
                self.active_quests[ctx.author.id]["wins"] += 1
            else:
                chat.add_user_message(f"I choose to {choice} but I fail...")
                outcome_text = "**Failure!** 😢 You were bested by the challenge."
                self.active_quests[ctx.author.id]["losses"] += 1

            # Show updated win/loss
            wins = self.active_quests[ctx.author.id]["wins"]
            losses = self.active_quests[ctx.author.id]["losses"]

            # Check end conditions FIRST
            if wins >= 3 or losses >= 3:
                # 🌟 Adventure is ending! No more reactions, no more choices.

                # Create a fresh summarizing chat
                summary_chat = Chat(
                    "You are a bard who summarizes completed adventures. "
                    "Create a short, satisfying ending for the user's quest without offering any new choices."
                )

                if wins >= 3:
                    summary_chat.add_user_message(
                        "The user completed their adventure with 3 victories."
                    )
                    xp_earned = random.randint(100, 150)
                    end_text = f"🏆 **Victory!** You conquered your adventure with {wins} successes!"
                else:
                    summary_chat.add_user_message(
                        "The user failed their adventure after 3 defeats."
                    )
                    xp_earned = random.randint(30, 50)
                    end_text = f"💀 **Defeat!** You fell after {losses} failures."

                # Get final story
                final_story = self.model.respond(summary_chat)

                await ctx.send(end_text)
                await ctx.send(final_story)

                # Award XP
                leveling_cog = self.bot.get_cog("Leveling")
                if leveling_cog:
                    await leveling_cog.add_xp(ctx.author.id, xp_earned)

                await ctx.send(f"You gained **{xp_earned} XP** from your journey!")

                # Clean up
                del self.active_quests[ctx.author.id]
                return  # End command here!

            # 🚀 Adventure still ongoing, continue
            continuation = self.model.respond(chat)

            # Send next adventure message (with reactions)
            message = await self.send_adventure_message(
                ctx,
                f"{outcome_text}\n\nWins: **{wins}**, Losses: **{losses}**\n\n{continuation}",
                add_reactions=True,
            )


async def setup(bot):
    await bot.add_cog(Adventure(bot))
