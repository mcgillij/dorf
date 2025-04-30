import aiohttp
import sqlite3
import logging
import asyncio

import discord
from discord.ext import commands, tasks
from bot.tools.searxng_search import search_source
from bot.constants import NEWS_DB
from bot.config import CHAT_CHANNEL_ID

logger = logging.getLogger(__name__)


class NewsAgent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = sqlite3.connect(NEWS_DB)
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
            username TEXT NOT NULL,
            location TEXT,
            country TEXT
        )
        """
        )
        cursor.execute(
            """
        CREATE TABLE IF NOT EXISTS preferences (
            user_id INTEGER,
            topic TEXT NOT NULL,
            source TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
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
            status TEXT NOT NULL
        )
        """
        )
        self.db.commit()

    async def cog_unload(self):
        self.check_tasks.cancel()  # Stop the periodic task when the cog is unloaded

    async def start_task(self, task_id: int, task_name: str, interval: int):
        """Start a task and update its status in the database."""

        async def task_runner():
            while True:
                await self.execute_task_logic(task_id, task_name)
                await asyncio.sleep(interval * 60)  # Wait for the next interval

        # Start the task and store it in the running_tasks dictionary
        task = asyncio.create_task(task_runner())
        self.running_tasks[task_id] = task

        # Update the task's status in the database
        self.update_task(task_id, status="running")

    async def stop_task(self, task_id: int):
        """Stop a running task and update its status in the database."""
        if task_id in self.running_tasks:
            self.running_tasks[task_id].cancel()
            del self.running_tasks[task_id]
            self.update_task(task_id, status="stopped")

    async def execute_task_logic(self, task_id: int, task_name: str):
        """Execute the logic for a scheduled task."""
        logger.info(f"Executing task {task_name} (ID: {task_id})")
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT user_id FROM scheduled_tasks WHERE id = ?",
            (task_id,),
        )
        user_id_row = cursor.fetchone()

        if not user_id_row:
            logger.warning(f"No user found for task {task_name} (ID: {task_id}).")
            return

        user_id = user_id_row[0]

        # Fetch preferences
        cursor.execute(
            "SELECT topic, source FROM preferences WHERE user_id = ?",
            (user_id,),
        )
        preferences = cursor.fetchall()

        if not preferences:
            logger.warning(
                f"No preferences found for task {task_name} (ID: {task_id})."
            )
            return

        # Fetch updates from sources
        results = []
        for topic, source in preferences:
            try:
                results.extend(await search_source(source, topic))
            except Exception as e:
                logger.error(
                    f"Error fetching updates for topic '{topic}' from source '{source}': {e}"
                )

        # Fetch weather data
        try:
            weather_embed = await self.fetch_weather_embed(user_id)
            weather_response = f"\n\nWeather updates:\n{weather_embed.title}\n{weather_embed.description}"
        except ValueError as e:
            weather_response = f"\n\nWeather updates: {str(e)}"
        except Exception:
            weather_response = "\n\nWeather updates: Failed to fetch weather data."

        # Prepare response
        if results:
            response = "\n".join(
                [f"**{res['title']}**: {res['url']}" for res in results[:5]]
            )
        else:
            response = f"No updates found for task '{task_name}'."

        response += weather_response

        # Notify the channel via Discord
        channel = self.bot.get_channel(CHAT_CHANNEL_ID)
        if channel:
            try:
                await channel.send(f"Updates for task '{task_name}':\n{response}")
            except Exception as e:
                logger.error(
                    f"Failed to send updates to channel {CHAT_CHANNEL_ID}: {e}"
                )
        else:
            logger.error(f"Channel with ID {CHAT_CHANNEL_ID} not found.")

    @tasks.loop(minutes=5)
    async def check_tasks(self):
        """Check the database for scheduled tasks and ensure they are running."""
        logger.info("checking for task")
        cursor = self.db.cursor()
        cursor.execute("SELECT id, task_name, interval, status FROM scheduled_tasks")
        scheduled_tasks = cursor.fetchall()

        for task_id, task_name, interval, status in scheduled_tasks:
            if status != "running" and task_id not in self.running_tasks:
                try:
                    await self.start_task(task_id, task_name, interval)
                except Exception as e:
                    print(f"Failed to start task {task_name}: {e}")

        # Stop tasks that are no longer in the database
        running_task_ids = set(self.running_tasks.keys())
        db_task_ids = {task[0] for task in scheduled_tasks}
        for task_id in running_task_ids - db_task_ids:
            await self.stop_task(task_id)

    @check_tasks.before_loop
    async def before_check_tasks(self):
        await self.bot.wait_until_ready()

    def add_task(self, task_name: str, interval: int, status: str = "pending"):
        """Add a new scheduled task to the database."""
        cursor = self.db.cursor()
        cursor.execute(
            """
        INSERT INTO scheduled_tasks (task_name, interval, status)
        VALUES (?, ?, ?)
        """,
            (task_name, interval, status),
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

    @commands.command(name="add_task")
    async def add_task_command(self, ctx, task_name: str, interval: int):
        """Add a new scheduled task to the database."""
        user_id = ctx.author.id
        cursor = self.db.cursor()

        # Check if the user has preferences or location set
        cursor.execute(
            "SELECT 1 FROM users WHERE user_id = ? AND location IS NOT NULL AND country IS NOT NULL",
            (user_id,),
        )
        user_has_location = cursor.fetchone()

        cursor.execute(
            "SELECT 1 FROM preferences WHERE user_id = ? LIMIT 1", (user_id,)
        )
        user_has_preferences = cursor.fetchone()

        if not user_has_location and not user_has_preferences:
            raise ValueError(
                "Cannot add a task without user preferences or location set."
            )

        self.add_task(task_name, interval)
        await ctx.send(
            f"Task '{task_name}' added with an interval of {interval} minutes."
        )

    @commands.command(name="remove_task")
    async def remove_task_command(self, ctx, task_id: int):
        """Remove a scheduled task by its ID."""
        self.remove_task(task_id)
        await ctx.send(f"Task with ID {task_id} has been removed.")

    @commands.command(name="list_tasks")
    async def list_tasks_command(self, ctx):
        """List all scheduled tasks."""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT id, task_name, interval, last_run, status FROM scheduled_tasks"
        )
        tasks = cursor.fetchall()

        if tasks:
            response = "\n".join(
                [
                    f"ID: {task[0]}, Name: {task[1]}, Interval: {task[2]} mins, Last Run: {task[3] or 'Never'}, Status: {task[4]}"
                    for task in tasks
                ]
            )
        else:
            response = "No scheduled tasks found."

        await ctx.send(response)

    @commands.command(name="add_topic")
    async def add_topic(self, ctx, topic: str, source: str):
        """Add a topic and optional source to the user's preferences."""
        user_id = ctx.author.id
        username = ctx.author.name

        cursor = self.db.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        cursor.execute(
            "INSERT INTO preferences (user_id, topic, source) VALUES (?, ?, ?)",
            (user_id, topic, source),
        )
        self.db.commit()

        await ctx.send(
            f"Added topic '{topic}' with source '{source or 'any'}' to your preferences."
        )

    @commands.command(name="list_topics")
    async def list_topics(self, ctx):
        """List all topics and sources in the user's preferences."""
        user_id = ctx.author.id
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT topic, source FROM preferences WHERE user_id = ?", (user_id,)
        )
        preferences = cursor.fetchall()

        if preferences:
            response = "\n".join(
                [
                    f"Topic: {topic}, Source: {source or 'any'}"
                    for topic, source in preferences
                ]
            )
        else:
            response = "You have no topics in your preferences."

        await ctx.send(response)

    async def get_user_prefs(self, user_id):
        """Retrieve the user's preferences for topics and sources."""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT topic, source FROM preferences WHERE user_id = ?", (user_id,)
        )
        preferences = cursor.fetchall()

        # Return a list of (topic, source) pairs
        return [(topic, source or None) for topic, source in preferences]

    @commands.command(name="news")
    async def news(self, ctx):
        """Search for news articles based on a query."""
        # Query user preferences
        user_preferences = await self.get_user_prefs(
            ctx.author.id
        )  # Assuming this function exists

        if not user_preferences:
            await ctx.send("No preferences found. Please set your preferences first.")
            return

        await ctx.send(f"Searching for news for you based on your preferences.")

        # Iterate over sources and topics from user preferences
        results = []
        for source, topic in user_preferences:
            results.extend(await search_source(source, topic))

        if results:
            response = "\n".join(
                [f"**{res['title']}**: {res['url']}" for res in results[:5]]
            )
        else:
            response = "No results found."

        await ctx.send(response)

    @commands.command(name="update_location")
    async def update_location(self, ctx, location: str, country: str):
        """Update the user's location."""
        user_id = ctx.author.id
        username = ctx.author.name
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        cursor.execute(
            "UPDATE users SET location = ?, country = ? WHERE user_id = ?",
            (location, country, user_id),
        )
        self.db.commit()

        await ctx.send(
            f"Your location has been updated to '{location}', and country={country}."
        )

    @commands.command(name="get_weather")
    async def get_weather(self, ctx):
        """Fetch the weather based on the user's location."""
        user_id = ctx.author.id
        try:
            weather_embed = await self.fetch_weather_embed(user_id)
            await ctx.send(embed=weather_embed)
        except ValueError as e:
            await ctx.send(str(e))
        except Exception:
            await ctx.send("Failed to fetch weather data. Please try again later.")

    async def fetch_weather_embed(self, user_id: int) -> discord.Embed:
        """Fetch weather data and return a Discord embed."""
        cursor = self.db.cursor()
        cursor.execute(
            "SELECT location, country FROM users WHERE user_id = ?", (user_id,)
        )
        result = cursor.fetchone()

        if not result or not result[0]:
            raise ValueError(
                "You need to set your location first using the `update_location` command."
            )

        location, country = result
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://wttr.in/{location} {country}?format=j1"
            ) as response:
                if response.status != 200:
                    raise Exception("Failed to fetch weather data.")

                weather_data = await response.json()
                current = weather_data["current_condition"][0]
                nearest_area = weather_data["nearest_area"][0]

                embed = discord.Embed(
                    title=f"Weather in {nearest_area['areaName'][0]['value']}, {nearest_area['country'][0]['value']}",
                    description=current["weatherDesc"][0]["value"],
                    color=discord.Color.blue(),
                )
                embed.add_field(
                    name="Temperature",
                    value=f"{current['temp_C']}°C / {current['temp_F']}°F",
                    inline=True,
                )
                embed.add_field(
                    name="Feels Like",
                    value=f"{current['FeelsLikeC']}°C / {current['FeelsLikeF']}°F",
                    inline=True,
                )
                embed.add_field(
                    name="Humidity", value=f"{current['humidity']}%", inline=True
                )
                embed.add_field(
                    name="Wind",
                    value=f"{current['windspeedKmph']} km/h ({current['winddir16Point']})",
                    inline=True,
                )
                embed.add_field(
                    name="Pressure", value=f"{current['pressure']} hPa", inline=True
                )
                embed.add_field(
                    name="Visibility", value=f"{current['visibility']} km", inline=True
                )
                embed.set_footer(text=f"Last updated: {current['localObsDateTime']}")

                return embed


async def setup(bot):
    await bot.add_cog(NewsAgent(bot))
    logger.info("NewsAgent cog loaded.")
