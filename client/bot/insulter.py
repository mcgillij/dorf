import sqlite3
import logging
import datetime
import asyncio
from typing import Optional

from discord.ext import commands, tasks
from bot.constants import INSULT_DB
from bot.config import CHAT_CHANNEL_ID
from bot.lms import qa_insult


logger = logging.getLogger(__name__)


class Insulter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = sqlite3.connect(INSULT_DB)
        self._initialize_db()
        self.running_tasks = {}  # Track running tasks by ID
        self.check_tasks.start()  # Start the periodic task

    def _initialize_db(self):
        """Initialize the SQLite database with required tables."""
        cursor = self.db.cursor()
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL
        )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                interval INTEGER NOT NULL,
                last_run TIMESTAMP,
                status TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
        )
        self.db.commit()

    async def cog_unload(self):
        self.check_tasks.cancel()  # Stop the periodic task when the cog is unloaded

    async def start_task(self, task_id: int, task_name: str, interval: int):
        """Start a task and update its status in the database."""
        logger.info("In start_task")

        async def task_runner():
            while True:
                await self.execute_task_logic(task_id, task_name)
                await asyncio.sleep(interval * 60)  # Wait for the next interval

        # Start the task and store it in the running_tasks dictionary
        task = asyncio.create_task(task_runner())
        self.running_tasks[task_id] = task

        # Update the task's status in the database
        self.update_task(task_id, status="running")

    def reset_tasks_to_pending(self):
        """
        Resets all tasks in the database to 'pending' status.
        This should be called when the bot starts to ensure tasks are restarted.
        """
        cursor = self.db.cursor()
        try:
            cursor.execute(
                "UPDATE scheduled_tasks SET status = 'pending' WHERE status = 'running'"
            )
            self.db.commit()
            logger.info("All running tasks have been reset to 'pending'.")
        except Exception as e:
            logger.error(f"Error resetting tasks to pending: {e}")

    async def stop_task(self, task_id: int):
        """Stop a running task and update its status in the database."""
        logger.info("In stop task")
        if task_id in self.running_tasks:
            self.running_tasks[task_id].cancel()
            del self.running_tasks[task_id]
            self.update_task(task_id, status="stopped")

    async def execute_task_logic(self, task_id: int, task_name: str):
        logger.info(f"Executing task {task_name} (ID: {task_id})")

        user_id = self.get_user_id(task_id, task_name)
        if not user_id:
            logger.info("userid not found")
            return

        qa_result = await qa_insult()
        channel = self.bot.get_channel(CHAT_CHANNEL_ID)
        if channel:
            await channel.send(f"<@1004346899156979753>: {qa_result}")
        self.update_task(task_id, last_run=datetime.datetime.now(), status="running")

    def get_user_id(self, task_id: int, task_name: str) -> Optional[int]:
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT user_id FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        user_id_row = cursor.fetchone()
        logger.debug(f"user_id_row: {user_id_row}")

        if not user_id_row:
            logger.warning(f"No user found for task {task_name} (ID: {task_id}).")
            return None

        user_id = user_id_row[0]
        logger.debug(f"user_id: {user_id}")
        return user_id

    @tasks.loop(minutes=1)
    async def check_tasks(self):
        """Check the database for scheduled tasks and ensure they are running."""
        # logger.info("Checking for tasks to start or stop.")
        cursor = self.db.cursor()
        cursor.execute("SELECT id, task_name, interval, status FROM scheduled_tasks")
        scheduled_tasks = cursor.fetchall()

        for task_id, task_name, interval, status in scheduled_tasks:
            if status != "running" and task_id not in self.running_tasks:
                try:
                    logger.info(f"Starting task: {task_name} (ID: {task_id})")
                    await self.start_task(task_id, task_name, interval)
                except Exception as e:
                    logger.error(
                        f"Failed to start task {task_name} (ID: {task_id}): {e}"
                    )

        # Stop tasks that are no longer in the database
        running_task_ids = set(self.running_tasks.keys())
        db_task_ids = {task[0] for task in scheduled_tasks}
        for task_id in running_task_ids - db_task_ids:
            logger.info(f"Stopping task with ID: {task_id}")
            await self.stop_task(task_id)

    @check_tasks.before_loop
    async def before_check_tasks(self):
        logger.info("In before_check")
        self.reset_tasks_to_pending()  # Reset tasks to 'pending' on bot startup
        await self.bot.wait_until_ready()

    def add_task(
        self, user_id: int, task_name: str, interval: int, status: str = "pending"
    ):
        """Add a new scheduled task to the database."""
        cursor = self.db.cursor()
        cursor.execute(
            """
            INSERT INTO scheduled_tasks (user_id, task_name, interval, status)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, task_name, interval, status),
        )
        self.db.commit()

    def remove_task(self, task_id: int):
        """Remove a scheduled task from the database."""
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        self.db.commit()

    def update_task(self, task_id: int, last_run=None, status=None):
        """Update the status or last_run of a scheduled task."""
        cursor = self.db.cursor()
        if last_run:
            cursor.execute(
                "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
                (last_run, task_id),
            )
        if status:
            cursor.execute(
                "UPDATE scheduled_tasks SET status = ? WHERE id = ?", (status, task_id)
            )
        self.db.commit()

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

        self.add_task(user_id, task_name, interval)
        await ctx.send(
            f"Task '{task_name}' added with an interval of {interval} minutes."
        )

    @commands.command(name="qa_remove_task")
    async def remove_task_command(self, ctx, task_id: int):
        """Remove task id. format: <id>:int"""
        """Remove a scheduled task by its ID."""
        self.remove_task(task_id)
        await ctx.send(f"Task with ID {task_id} has been removed.")

    @commands.command(name="qa_list_tasks")
    async def list_tasks_command(self, ctx):
        """List all scheduled tasks."""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT id, task_name, interval, last_run, status, user_id FROM scheduled_tasks"
        )
        tasks = cursor.fetchall()

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
    logger.info("NewsAgent cog loaded.")
