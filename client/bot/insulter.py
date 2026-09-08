import logging
import datetime

from discord.ext import commands

from bot.config import CHAT_CHANNEL_ID
from bot.constants import INSULT_DB
from bot.lms import qa_insult
from bot.scheduled_base import ScheduledTaskCog

logger = logging.getLogger(__name__)


class Insulter(ScheduledTaskCog):
    def __init__(self, bot):
        super().__init__(bot, INSULT_DB)

    def _initialize_extra_tables(self):
        """Insulter-specific tables (scheduled_tasks lives in the base)."""
        cursor = self.db.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL
        )
        """)

    async def execute_task_logic(self, task_id: int, task_name: str):
        logger.info(f"Executing task {task_name} (ID: {task_id})")

        user_id = self.get_user_id(task_id, task_name)
        if not user_id:
            logger.info("userid not found")
            return

        qa_result = await qa_insult()
        channel = self.bot.get_channel(CHAT_CHANNEL_ID)
        if channel:
            await channel.send(f"<@{user_id}>: {qa_result}")
        self.update_task(task_id, last_run=datetime.datetime.now(), status="running")

    @commands.command(name="qa_add_task")
    async def add_task_command(self, ctx, task_name: str, interval: int):
        """Add a new scheduled task to the database. format: <name>:str <interval>:int(in minutes)"""
        user_id = ctx.author.id
        username = ctx.author.name
        cursor = self.db.cursor()

        # Check if the user exists
        cursor.execute(
            "SELECT 1 FROM users WHERE user_id = ?",
            (user_id,),
        )
        user_exists = cursor.fetchone()

        if not user_exists:
            logger.info("No user")
            cursor = self.db.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )

        self.add_task(user_id, task_name, max(5, interval))
        await ctx.send(
            f"Task '{task_name}' added with an interval of {max(5, interval)} minutes."
        )

    @commands.command(name="qa_remove_task")
    async def remove_task_command(self, ctx, task_id: int):
        """Remove task id. format: <id>:int"""
        """Remove a scheduled task by its ID."""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT user_id FROM scheduled_tasks WHERE id = ?", (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send(f"No task with ID {task_id}.")
            return
        is_owner = row[0] == ctx.author.id
        is_mod = bool(ctx.guild and ctx.author.guild_permissions.manage_guild)
        if not (is_owner or is_mod):
            await ctx.send("You can only remove your own tasks.")
            return
        self.remove_task(task_id)
        await ctx.send(f"Task with ID {task_id} has been removed.")

    @commands.command(name="qa_list_tasks")
    async def list_tasks_command(self, ctx):
        """List all scheduled tasks."""
        tasks = self.list_task_rows()

        if tasks:
            response = "\n".join(
                [
                    f"ID: {task[0]}, Name: {task[1]}, Interval: {task[2]} mins, Last Run: {task[3] or 'Never'}, Status: {task[4]}, User ID: {task[5]}"
                    for task in tasks
                ]
            )
        else:
            response = "No scheduled tasks found."

        await ctx.send(response)


async def setup(bot):
    await bot.add_cog(Insulter(bot))
    logger.info("Insulter cog loaded.")
