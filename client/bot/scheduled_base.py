"""Shared DB-backed scheduler for scheduled-task cogs (news, insulter).

The two cogs carried ~180 identical lines that drifted apart over time: one
gained a task cap and wait_until_ready, the other didn't, and commit behavior
differed. This base owns the reconciliation loop and runner lifecycle;
subclasses implement execute_task_logic (and their own tables/commands).
"""
import asyncio
import logging
from typing import Optional

from discord.ext import commands, tasks

from bot.db import open_db

logger = logging.getLogger(__name__)

SCHEDULED_TASKS_DDL = """
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


class ScheduledTaskCog(commands.Cog):
    # Reconciliation sweep cadence and the floor for user-requested intervals.
    CHECK_INTERVAL_MINUTES = 1
    MIN_INTERVAL_MINUTES = 5

    def __init__(self, bot, db_path):
        self.bot = bot
        self.db = open_db(db_path)
        self._initialize_tables()
        self.running_tasks = {}  # task_id -> asyncio.Task
        # Per-instance Loop: a class-level @tasks.loop would be shared by all
        # subclasses and refuse to start twice.
        self.check_tasks = tasks.loop(minutes=self.CHECK_INTERVAL_MINUTES)(
            self._check_tasks_tick
        )
        self.check_tasks.before_loop(self._before_check_tasks_tick)
        self.check_tasks.start()

    # --- subclass hooks -----------------------------------------------------

    def _initialize_extra_tables(self) -> None:
        """Create cog-specific tables (users, preferences, ...)."""

    async def execute_task_logic(self, task_id: int, task_name: str):
        """Run one interval of the task. Must be overridden."""
        raise NotImplementedError

    # --- DDL ---------------------------------------------------------------

    def _initialize_tables(self):
        """Initialize the SQLite database with required tables."""
        cursor = self.db.cursor()
        cursor.execute(SCHEDULED_TASKS_DDL)
        self._initialize_extra_tables()
        self.db.commit()

    # --- runner lifecycle ----------------------------------------------------

    async def cog_unload(self):
        self.check_tasks.cancel()  # Stop the periodic task when the cog is unloaded
        for task in list(self.running_tasks.values()):
            task.cancel()
        self.running_tasks.clear()

    async def start_task(self, task_id: int, task_name: str, interval: int):
        """Start a task and update its status in the database."""
        interval = max(self.MIN_INTERVAL_MINUTES, interval)
        sleep_s = interval * 60

        async def task_runner():
            # Sleep first: running immediately re-fired the task on every
            # restart even if it ran minutes before shutdown.
            while True:
                await asyncio.sleep(sleep_s)
                try:
                    await self.execute_task_logic(task_id, task_name)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Task run failed (id=%s, name=%s)", task_id, task_name
                    )

        task = asyncio.create_task(task_runner())
        self.running_tasks[task_id] = task
        self.update_task(task_id, status="running")

    async def stop_task(self, task_id: int):
        """Stop a running task and update its status in the database."""
        if task_id in self.running_tasks:
            self.running_tasks[task_id].cancel()
            del self.running_tasks[task_id]
            self.update_task(task_id, status="stopped")

    def reset_tasks_to_pending(self):
        """Reset all 'running' tasks so the next sweep restarts them.

        Called on startup: the bot may have been killed mid-interval and the
        task must run again rather than sleep forever as a zombie 'running'.
        """
        try:
            cursor = self.db.cursor()
            cursor.execute(
                "UPDATE scheduled_tasks SET status = 'pending' WHERE status = 'running'"
            )
            self.db.commit()
            logger.info("All running tasks have been reset to 'pending'.")
        except Exception as e:
            logger.error(f"Error resetting tasks to pending: {e}")

    def get_user_id(self, task_id: int, task_name: str) -> Optional[int]:
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT user_id FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            logger.warning(f"No user found for task {task_name} (ID: {task_id}).")
            return None
        return row[0]

    # --- reconciliation loop -------------------------------------------------

    async def _check_tasks_tick(self):
        """Check the database for scheduled tasks and ensure they are running."""
        cursor = self.db.cursor()
        cursor.execute("SELECT id, task_name, interval, status FROM scheduled_tasks")
        scheduled_tasks = cursor.fetchall()

        for task_id, task_name, interval, status in scheduled_tasks:
            # Reap dead runners so the watchdog can restart them
            runner = self.running_tasks.get(task_id)
            if runner and runner.done():
                del self.running_tasks[task_id]
                if not runner.cancelled() and runner.exception():
                    logger.error(
                        "Task runner died (id=%s, name=%s): %r",
                        task_id,
                        task_name,
                        runner.exception(),
                    )
                self.update_task(task_id, status="pending")
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

    async def _before_check_tasks_tick(self):
        logger.info("scheduled tasks: startup sweep")
        self.reset_tasks_to_pending()
        # Wait for the gateway: starting runners before READY made them fire
        # into channels we can't resolve yet.
        await self.bot.wait_until_ready()

    # --- CRUD -----------------------------------------------------------------

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

    def list_task_rows(self):
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT id, task_name, interval, last_run, status, user_id "
            "FROM scheduled_tasks"
        )
        return cursor.fetchall()
