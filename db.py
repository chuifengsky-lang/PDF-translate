"""SQLite cache for translations and word definitions.

Keyed so that re-opening the same paper reuses prior LLM output instead of
paying for it again.
"""

import sqlite3
import time
import os
import sys
import threading


def _app_dir():
    """Folder for persistent files (cache.db). When packaged by PyInstaller the
    script lives in a temp dir that's wiped on exit, so use the exe's folder."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(_app_dir(), "cache.db")


class Cache:
    def __init__(self, path=DB_PATH):
        # check_same_thread=False: the connection is used from background
        # QThreads (translation / word lookup). All access is serialized by
        # self._lock so concurrent use is safe.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
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
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT translation FROM translations WHERE doc=? AND block_id=? AND model=?",
                (doc, block_id, model),
            )
            row = c.fetchone()
            return row[0] if row else None

    def set_translation(self, doc, block_id, model, original, translation):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO translations VALUES (?, ?, ?, ?, ?, ?)",
                (block_id, doc, model, original, translation, int(time.time())),
            )
            self.conn.commit()

    def clear_doc(self, doc):
        """Delete all cached translations for a document so re-opening it shows
        the original (no auto-filled translations)."""
        with self._lock:
            self.conn.execute("DELETE FROM translations WHERE doc=?", (doc,))
            self.conn.commit()

    # --- word definitions ---
    def get_word(self, word, model):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT definition FROM word_cache WHERE word=? AND model=?",
                (word.lower(), model),
            )
            row = c.fetchone()
            return row[0] if row else None

    def set_word(self, word, model, definition):
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO word_cache VALUES (?, ?, ?, ?)",
                (word.lower(), model, definition, int(time.time())),
            )
            self.conn.commit()

    def close(self):
        self.conn.close()
