import logging
import sqlite3
from discord.ext import commands

logger = logging.getLogger(__name__)
from bot.config import AvatarState
from bot.constants import AVATAR_STATE_DB_PATH


class StateManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = AVATAR_STATE_DB_PATH
        self._initialize_database()

    def _initialize_database(self):
        """Creates the database and table if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS avatar_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL CHECK(state IN ('idle', 'talking', 'thinking', 'drawing')),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.commit()
        cursor.execute(
            "INSERT INTO avatar_state (state) VALUES (?)", (AvatarState.IDLE.value,)
        )
        conn.commit()
        conn.close()

    def update_state(self, state: AvatarState):
        """Updates the avatar state in the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO avatar_state (state) VALUES (?)", (state.value,))
        conn.commit()
        conn.close()

    def get_current_state(self) -> str:
        """Retrieves the most recent avatar state from the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT state FROM avatar_state ORDER BY updated_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else AvatarState.IDLE.value


async def setup(bot):
    await bot.add_cog(StateManager(bot))
    logger.info("StateManager Cog loaded.")
