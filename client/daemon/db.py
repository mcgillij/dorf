import sqlite3
from datetime import datetime

class SQLiteDB:
    def __init__(self, db_name='voice_responses.db'):
        self.db_name = db_name

    def create_table(self):
        """Create the voice_responses table if it doesn't exist."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS voice_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    message TEXT,
                    datetime TEXT
                )
            ''')

    def insert_entry(self, user_id: str, message: str):
        """Insert a new entry into the table."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO voice_responses(user_id, message, datetime)
                VALUES(?, ?, ?)
            ''', (user_id, message.strip(), timestamp))

    def get_all_entries(self):
        """Retrieve all entries from the table."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM voice_responses')
            rows = cursor.fetchall()
            return rows

