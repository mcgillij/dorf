import sqlite3
import logging
from contextlib import closing
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Files the bot writes per message/reaction. WAL + busy_timeout make
# concurrent writers (bot + whisper worker + external Godot client reading
# avatar_state.db) wait instead of failing with "database is locked".
# journal_mode=WAL is persistent per database file, so applying it once at
# startup fixes every later connection.
SQLITE_FILES = (
    "voice_responses.db",
    "avatar_state.db",
    "emojis.db",
    "macros.db",
    "faction_data.db",
    "metrics.db",
    "news_agent.db",
    "insult.db",
    "xp_users.db",
    "quotes.db",
)


def ensure_sqlite_pragmas(paths=SQLITE_FILES) -> None:
    for path in paths:
        try:
            with closing(sqlite3.connect(path, timeout=5)) as conn:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                conn.execute("PRAGMA busy_timeout=5000")
                conn.commit()
            if str(mode).lower() != "wal":
                logger.warning("sqlite WAL not enabled for %s (mode=%s)", path, mode)
            else:
                logger.info("sqlite pragmas applied: %s", path)
        except Exception:
            logger.exception("sqlite pragma setup failed for %s", path)


class SQLiteDB:
    def __init__(self, db_name="voice_responses.db"):
        logger.info("initializing the database")
        self.db_name = db_name

    def create_table(self):
        """Create the voice_responses table if it doesn't exist."""
        logger.info("Creating table")
        with closing(sqlite3.connect(self.db_name)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS voice_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    message TEXT,
                    datetime TEXT
                )
            """)
            conn.commit()

    def insert_entry(self, user_id: str, message: str):
        """Insert a new entry into the table."""
        logger.info("Inserting entry into the db")
        # UTC, unambiguous, joinable with the other DBs' timestamps.
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db_name)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO voice_responses(user_id, message, datetime)
                VALUES(?, ?, ?)
            """,
                (user_id, message.strip(), timestamp),
            )
            conn.commit()
