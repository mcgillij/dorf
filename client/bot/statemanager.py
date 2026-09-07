import logging
import sqlite3
from contextlib import closing

from discord.ext import commands

from bot.config import AvatarState
from bot.constants import AVATAR_STATE_DB_PATH

logger = logging.getLogger(__name__)


def _initialize_database(db_path=AVATAR_STATE_DB_PATH):
    """Creates the database and table if they don't exist."""
    with closing(sqlite3.connect(db_path)) as conn:
        cursor = conn.cursor()
        # Wait instead of failing with "database is locked" when another
        # process holds a write lock on the shared DB file.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS avatar_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL CHECK(state IN ('idle', 'talking', 'thinking', 'drawing')),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        # Only seed IDLE when the table is empty; seeding on every cog load
        # appended fake state events that could shadow the real latest state.
        cursor.execute("SELECT COUNT(*) FROM avatar_state")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                # test entries
                # "INSERT INTO avatar_state (state) VALUES (?)", (AvatarState.TALKING.value,)
                # "INSERT INTO avatar_state (state) VALUES (?)", (AvatarState.THINKING.value,)
                "INSERT INTO avatar_state (state) VALUES (?)",
                (AvatarState.IDLE.value,),
            )
            conn.commit()


def update_state(state: AvatarState):
    """Updates the avatar state in the database."""
    # getattr() also accepts plain strings ("talking") alongside AvatarState
    # members, so standalone callers don't have to build the enum.
    state_value = getattr(state, "value", state)
    with closing(sqlite3.connect(AVATAR_STATE_DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("INSERT INTO avatar_state (state) VALUES (?)", (state_value,))
        conn.commit()
        # The table is append-only and grew unbounded (1 row per state
        # change); prune to the most recent 100 after each insert.
        cursor.execute(
            "DELETE FROM avatar_state WHERE id NOT IN "
            "(SELECT id FROM avatar_state ORDER BY id DESC LIMIT 100)"
        )
        conn.commit()


def get_current_state() -> str:
    """Retrieves the most recent avatar state from the database."""
    with closing(sqlite3.connect(AVATAR_STATE_DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        # Order by the AUTOINCREMENT id, not updated_at: CURRENT_TIMESTAMP
        # has 1-second resolution, so same-second writes tie and the winner
        # is arbitrary (a stale state could win).
        cursor.execute("SELECT state FROM avatar_state ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else AvatarState.IDLE.value


class StateManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = AVATAR_STATE_DB_PATH
        self._initialize_database()

    def _initialize_database(self):
        """Creates the database and table if they don't exist."""
        _initialize_database(self.db_path)

    def update_state(self, state: AvatarState):
        """Updates the avatar state in the database."""
        update_state(state)

    def update_state_idle(self):
        logger.info("Updating state to IDLE")
        self.update_state(AvatarState.IDLE)

    def update_state_thinking(self):
        logger.info("Updating state to THINKING")
        self.update_state(AvatarState.THINKING)

    def update_state_talking(self):
        logger.info("Updating state to TALKING")
        self.update_state(AvatarState.TALKING)

    def update_state_drawing(self):
        logger.info("Updating state to DRAWING")
        self.update_state(AvatarState.DRAWING)

    def get_current_state(self) -> str:
        """Retrieves the most recent avatar state from the database."""
        return get_current_state()


async def setup(bot):
    await bot.add_cog(StateManager(bot))
    logger.info("StateManager Cog loaded.")
