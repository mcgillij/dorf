import sqlite3
import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone

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

BUSY_TIMEOUT_MS = 5000


def open_db(path: str, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a SQLite connection with a per-connection busy timeout.

    journal_mode=WAL is persistent per database file (applied once at startup
    by ensure_sqlite_pragmas), but busy_timeout is per-connection — without it
    every short-lived connection fails instantly with "database is locked"
    when another writer (bot, whisper worker, external Godot client) holds the
    file. Always open connections through this helper.
    """
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def ensure_sqlite_pragmas(paths=SQLITE_FILES) -> None:
    for path in paths:
        try:
            with closing(open_db(path)) as conn:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                conn.commit()
            if str(mode).lower() != "wal":
                logger.warning("sqlite WAL not enabled for %s (mode=%s)", path, mode)
            else:
                logger.info("sqlite pragmas applied: %s", path)
        except Exception:
            logger.exception("sqlite pragma setup failed for %s", path)


def sweep_voice_responses(path: str = "voice_responses.db", max_age_days: int = 30) -> int:
    """Prune transcript history older than max_age_days. Returns rows deleted.

    voice_responses.db grows one row per transcribed utterance forever; this
    is called from the whisper worker's hourly retention sweep. The datetime
    column is UTC 'YYYY-MM-DD HH:MM:SS' strings — lexical comparison is exact.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with closing(open_db(path)) as conn:
            cursor = conn.execute(
                "DELETE FROM voice_responses WHERE datetime < ?", (cutoff,)
            )
            conn.commit()
            return cursor.rowcount if cursor.rowcount > 0 else 0
    except Exception:
        logger.exception("voice_responses sweep failed for %s", path)
        return 0


class SQLiteDB:
    def __init__(self, db_name="voice_responses.db"):
        logger.info("initializing the database")
        self.db_name = db_name

    def create_table(self):
        """Create the voice_responses table if it doesn't exist."""
        logger.info("Creating table")
        with closing(open_db(self.db_name)) as conn:
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
        with closing(open_db(self.db_name)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO voice_responses(user_id, message, datetime)
                VALUES(?, ?, ?)
            """,
                (user_id, message.strip(), timestamp),
            )
            conn.commit()
