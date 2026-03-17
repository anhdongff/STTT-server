"""SQLite DB helper for executing SQL statements using Python's built-in sqlite3.

Provides:
- SqliteDB class: connect(), close(), execute(), fetchone(), fetchall(), transaction(), execute_many()
- Automatically initializes the database using the bundled `sqlite_schema.sql` when the DB file doesn't exist.

Usage example:

from data_service.sqlite_db import SqliteDB
with SqliteDB.from_env() as db:
    rows = db.fetchall("SELECT * FROM users WHERE id = ?", (1,))

"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple, Iterator
from contextlib import contextmanager


class SqliteDB:
    def __init__(self, db_path: Optional[str] = None):
        """Create instance. db_path may be a string or Path. If omitted, uses SQLITE_DB_PATH env or ./data.sqlite3."""
        self._db_path = Path(db_path) if db_path else Path(os.getenv("SQLITE_DB_PATH", Path.cwd() / "data.sqlite3"))
        self._conn: Optional[sqlite3.Connection] = None

    @classmethod
    def from_env(cls) -> "SqliteDB":
        """Construct from environment; respects SQLITE_DB_PATH."""
        return cls(os.getenv("SQLITE_DB_PATH"))

    def _initialize_database_if_missing(self) -> None:
        if self._db_path.exists():
            return
        # Ensure parent exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).parent / "sqlite_schema.sql"
        # If schema file exists, run it to initialize the DB
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            if schema_path.is_file():
                sql = schema_path.read_text(encoding="utf-8")
                conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()

    def connect(self) -> None:
        if self._conn is None:
            self._initialize_database_if_missing()
            self._conn = sqlite3.connect(str(self._db_path), isolation_level=None)
            # return rows as mapping (dict-like)
            self._conn.row_factory = sqlite3.Row
            # enable foreign keys
            try:
                self._conn.execute("PRAGMA foreign_keys = ON;")
                self._conn.execute("PRAGMA journal_mode=WAL;")
            except Exception as e:
                print("Schema error:", e)
                raise

    def close(self) -> None:
        if self._conn:
            try:
                if self._conn:
                    self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "SqliteDB":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # if exception, rollback; otherwise commit
        if self._conn:
            if exc is not None:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            else:
                try:
                    self._conn.commit()
                except Exception:
                    pass
        self.close()

    def cursor(self) -> sqlite3.Cursor:
        self.connect()
        return self._conn.cursor()

    def execute(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> None:
        q = self._translate_query(query)

        cur = self._conn.cursor()

        try:
            if params is None:
                cur.execute(q)
            else:
                cur.execute(q, self._normalize_params(params))

        except Exception:
            self._conn.rollback()
            raise

        finally:
            cur.close()

    def fetchone(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> Optional[dict]:
        cur = self.cursor()
        try:
            q = self._translate_query(query)
            if params is None:
                cur.execute(q)
            else:
                cur.execute(q, self._normalize_params(params))
            row = cur.fetchone()
            return dict(row) if row is not None else None
        finally:
            cur.close()

    def fetchall(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> List[dict]:
        cur = self.cursor()
        try:
            q = self._translate_query(query)
            if params is None:
                cur.execute(q)
            else:
                cur.execute(q, self._normalize_params(params))
            return [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()

    @contextmanager
    def transaction(self) -> Iterator["SqliteDB"]:
        """Context manager for explicit transaction block.

        Usage:
        with db.transaction():
            db.execute(...)
            db.execute(...)
        """
        self.connect()
        try:
            yield self
            # commit after successful block
            if self._conn:
                self._conn.commit()
        except Exception:
            if self._conn:
                self._conn.rollback()
            raise

    def execute_many(self, query: str, seq_of_params: List[Tuple[Any, ...]]) -> None:
        cur = self.cursor()
        try:
            q = self._translate_query(query)
            # normalize each param sequence
            seq = [self._normalize_params(p) for p in seq_of_params]
            cur.executemany(q, seq)
        finally:
            cur.close()

    def _translate_query(self, query: str) -> str:
        """Translate psycopg2-style '%s' placeholders to sqlite3-style '?' placeholders.

        This allows code written for Postgres using %s to work with the SqliteDB helper.
        """
        if not query:
            return query
        return query.replace('%s', '?')

    def _normalize_params(self, params: Tuple[Any, ...]) -> Tuple[Any, ...]:
        """Convert parameters to types acceptable by sqlite3.

        - Path -> str
        - Other non-primitive types -> str
        """
        new = []
        for p in params:
            if p is None or isinstance(p, (str, int, float, bytes)):
                new.append(p)
            elif isinstance(p, Path):
                new.append(str(p))
            else:
                # fallback: convert to string
                new.append(str(p))
        return tuple(new)
