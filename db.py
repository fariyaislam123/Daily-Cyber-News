import sqlite3

DB = "news.db"

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            source TEXT,
            region TEXT,
            published_at TEXT,
            raw_text TEXT,
            category TEXT,
            severity TEXT,
            bullets TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")