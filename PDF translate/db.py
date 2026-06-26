"""SQLite cache for translations and word definitions.

Keyed so that re-opening the same paper reuses prior LLM output instead of
paying for it again.
"""

import sqlite3
import time
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.db")


class Cache:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                block_id TEXT,
                doc TEXT,
                model TEXT,
                original TEXT,
                translation TEXT,
                created_at INTEGER,
                PRIMARY KEY (doc, block_id, model)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS word_cache (
                word TEXT,
                model TEXT,
                definition TEXT,
                created_at INTEGER,
                PRIMARY KEY (word, model)
            )
        """)
        self.conn.commit()

    # --- translations ---
    def get_translation(self, doc, block_id, model):
        c = self.conn.cursor()
        c.execute(
            "SELECT translation FROM translations WHERE doc=? AND block_id=? AND model=?",
            (doc, block_id, model),
        )
        row = c.fetchone()
        return row[0] if row else None

    def set_translation(self, doc, block_id, model, original, translation):
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?, ?)",
            (block_id, doc, model, original, translation, int(time.time())),
        )
        self.conn.commit()

    # --- word definitions ---
    def get_word(self, word, model):
        c = self.conn.cursor()
        c.execute(
            "SELECT definition FROM word_cache WHERE word=? AND model=?",
            (word.lower(), model),
        )
        row = c.fetchone()
        return row[0] if row else None

    def set_word(self, word, model, definition):
        c = self.conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO word_cache VALUES (?, ?, ?, ?)",
            (word.lower(), model, definition, int(time.time())),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
