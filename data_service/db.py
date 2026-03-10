"""Postgres DB helper for executing SQL statements.

Simple, minimal synchronous helper using psycopg2. Provides:
- PostgresDB class: connect(), close(), execute(), fetchone(), fetchall(), transaction()
- Uses parameterized queries to avoid SQL injection.

Usage example:

from data_service.db import PostgresDB
with PostgresDB.from_env() as db:
    rows = db.fetchall("SELECT * FROM sttt.jobs WHERE id = %s", (1,))

"""
from __future__ import annotations
import os
import psycopg2
import psycopg2.extras
from pathlib import Path
from typing import Any, List, Optional, Tuple, Iterator, ContextManager
from contextlib import contextmanager


class PostgresDB:
    def __init__(self, dsn: Optional[str] = None, **connect_kwargs: Any):
        """Create instance. Provide either dsn or connect_kwargs (user, password, host, port, dbname).
        Example connect_kwargs keys: user, password, host, port, dbname
        """
        self._dsn = dsn
        self._connect_kwargs = connect_kwargs
        self._conn: Optional[psycopg2.extensions.connection] = None

    @classmethod
    def from_env(cls) -> "PostgresDB":
        # If environment variables are not exported, try to load .env from repository root
        # without overwriting already-exported variables.
        required_keys = [
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
        ]

        # find .env by walking up from CWD
        def find_dotenv(start: Optional[str] = None) -> Optional[Path]:
            cur = Path(start or os.getcwd()).resolve()
            root = cur.root
            while True:
                candidate = cur / '.env'
                if candidate.is_file():
                    return candidate
                if str(cur) == root:
                    return None
                cur = cur.parent

        def load_dotenv_file(path: Path) -> None:
            try:
                for line in path.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    # do not overwrite existing environment variables
                    os.environ.setdefault(k, v)
            except Exception:
                # silently ignore parse errors
                return

        # attempt to load .env if any required key missing
        missing = [k for k in required_keys if k not in os.environ]
        if missing:
            dotenv = find_dotenv()
            if dotenv:
                load_dotenv_file(dotenv)

        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        dbname = os.getenv("POSTGRES_DB", "appdb")
        return cls(user=user, password=password, host=host, port=port, dbname=dbname)

    def connect(self) -> None:
        if self._conn is None or self._conn.closed:
            if self._dsn:
                self._conn = psycopg2.connect(self._dsn)
            else:
                self._conn = psycopg2.connect(**self._connect_kwargs)
            # set autocommit to False to manage transactions explicitly
            self._conn.autocommit = False

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "PostgresDB":
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

    def cursor(self) -> psycopg2.extensions.cursor:
        self.connect()
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> None:
        """Execute a statement (INSERT/UPDATE/DELETE) without returning rows."""
        cur = self.cursor()
        try:
            cur.execute(query, params)
        finally:
            cur.close()

    def fetchone(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> Optional[dict]:
        cur = self.cursor()
        try:
            cur.execute(query, params)
            return cur.fetchone()
        finally:
            cur.close()

    def fetchall(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> List[dict]:
        cur = self.cursor()
        try:
            cur.execute(query, params)
            return cur.fetchall()
        finally:
            cur.close()

    @contextmanager
    def transaction(self) -> Iterator["PostgresDB"]:
        """Context manager for explicit transaction block.

        Usage:
        with db.transaction():
            db.execute(...)
            db.execute(...)
        """
        self.connect()
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def execute_many(self, query: str, seq_of_params: List[Tuple[Any, ...]]) -> None:
        cur = self.cursor()
        try:
            psycopg2.extras.execute_batch(cur, query, seq_of_params)
        finally:
            cur.close()
