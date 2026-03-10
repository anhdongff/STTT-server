"""Simple SQL builder helpers: SelectBuilder, InsertBuilder, UpdateBuilder, DeleteBuilder

These builders produce parameterized SQL strings and a tuple/list of params suitable for psycopg2.
They are intentionally small and minimal — useful for dynamically composing queries while keeping parameters separate.

Example:

q, params = SelectBuilder('sttt.jobs')\
    .select('id', 'status')\
    .where("user_id = %s", (123,))\
    .where("status = %s", ('pending',))\
    .order_by('created_at DESC')\
    .limit(10)
    .build()

db.fetchall(q, params)
"""
from __future__ import annotations
from typing import Any, List, Tuple, Optional


class SelectBuilder:
    def __init__(self, table: str):
        self.table = table
        self._selects: List[str] = []
        self._where: List[str] = []
        self._params: List[Any] = []
        self._order_by: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    def select(self, *columns: str) -> "SelectBuilder":
        if columns:
            self._selects.extend(columns)
        return self

    def where(self, clause: str, params: Optional[Tuple[Any, ...]] = None) -> "SelectBuilder":
        self._where.append(clause)
        if params:
            self._params.extend(list(params))
        return self

    def order_by(self, clause: str) -> "SelectBuilder":
        self._order_by = clause
        return self

    def limit(self, value: int) -> "SelectBuilder":
        self._limit = value
        return self

    def offset(self, value: int) -> "SelectBuilder":
        self._offset = value
        return self

    def build(self) -> Tuple[str, Tuple[Any, ...]]:
        select_clause = ", ".join(self._selects) if self._selects else "*"
        sql = f"SELECT {select_clause} FROM {self.table}"
        if self._where:
            sql += " WHERE " + " AND ".join(f"({w})" for w in self._where)
        if self._order_by:
            sql += f" ORDER BY {self._order_by}"
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        if self._offset is not None:
            sql += f" OFFSET {self._offset}"
        return sql, tuple(self._params)


class InsertBuilder:
    def __init__(self, table: str):
        self.table = table
        self._columns: List[str] = []
        self._values: List[Any] = []

    def set(self, column: str, value: Any) -> "InsertBuilder":
        self._columns.append(column)
        self._values.append(value)
        return self

    def build(self) -> Tuple[str, Tuple[Any, ...]]:
        if not self._columns:
            raise ValueError("No columns set for insert")
        cols = ", ".join(self._columns)
        placeholders = ", ".join(["%s"] * len(self._values))
        sql = f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})"
        return sql, tuple(self._values)


class UpdateBuilder:
    def __init__(self, table: str):
        self.table = table
        self._sets: List[str] = []
        self._params: List[Any] = []
        self._where: List[str] = []

    def set(self, column: str, value: Any) -> "UpdateBuilder":
        self._sets.append(f"{column} = %s")
        self._params.append(value)
        return self

    def where(self, clause: str, params: Optional[Tuple[Any, ...]] = None) -> "UpdateBuilder":
        self._where.append(clause)
        if params:
            self._params.extend(list(params))
        return self

    def build(self) -> Tuple[str, Tuple[Any, ...]]:
        if not self._sets:
            raise ValueError("No SET clauses for update")
        sql = f"UPDATE {self.table} SET " + ", ".join(self._sets)
        if self._where:
            sql += " WHERE " + " AND ".join(f"({w})" for w in self._where)
        return sql, tuple(self._params)


class DeleteBuilder:
    def __init__(self, table: str):
        self.table = table
        self._where: List[str] = []
        self._params: List[Any] = []

    def where(self, clause: str, params: Optional[Tuple[Any, ...]] = None) -> "DeleteBuilder":
        self._where.append(clause)
        if params:
            self._params.extend(list(params))
        return self

    def build(self) -> Tuple[str, Tuple[Any, ...]]:
        sql = f"DELETE FROM {self.table}"
        if self._where:
            sql += " WHERE " + " AND ".join(f"({w})" for w in self._where)
        return sql, tuple(self._params)
