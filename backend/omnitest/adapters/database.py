"""Read-only SQLite observations with authorization and bounded queries."""

import asyncio
import contextlib
import json
import sqlite3
import threading
import time

from pydantic import Field

from omnitest.core.ir import Model
from omnitest.core.security import contained

from .base import AdapterInfo


class Query(Model):
    sql: str = Field(min_length=1, max_length=10000)
    parameters: dict | list = Field(default_factory=dict)
    max_rows: int = Field(default=100, ge=1, le=1000)
    query_timeout: float = Field(default=5, ge=0.01, le=30)


class DatabaseAdapter:
    info = AdapterInfo(
        "database",
        "1.0",
        ["query", "parameterized_sql", "rows", "scalar"],
        ["SQLite fixture under administrator database_root"],
        ["database"],
    )

    async def check(self, context):
        path = contained(context.settings.database_root, context.target["config"]["path"])
        if not path.is_file():
            raise ValueError("Database fixture does not exist")
        self.connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, check_same_thread=False, timeout=1
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 10000)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 100)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
        self.connection.set_authorizer(self.authorize)
        self.cancelled = threading.Event()

    @staticmethod
    def authorize(action, first, second, database, trigger):
        allowed = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE,
        }
        if (
            action not in allowed
            or action == sqlite3.SQLITE_FUNCTION
            and (second or "").casefold() == "load_extension"
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    async def execute(self, step, context):
        if step.action != "query":
            raise ValueError("Database adapter supports query only")
        query = Query.model_validate(step.config)
        worker = asyncio.create_task(asyncio.to_thread(self.query, query))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.cancelled.set()
            self.connection.interrupt()
            with contextlib.suppress(Exception):
                await worker
            raise

    def query(self, query):
        deadline = time.monotonic() + query.query_timeout
        self.connection.set_progress_handler(
            lambda: int(self.cancelled.is_set() or time.monotonic() >= deadline), 1000
        )
        cursor = self.connection.execute(query.sql, query.parameters)
        try:
            values = cursor.fetchmany(query.max_rows + 1)
            truncated = len(values) > query.max_rows
            rows = [
                {
                    key: value.hex() if isinstance(value, bytes) else value
                    for key, value in dict(row).items()
                }
                for row in values[: query.max_rows]
            ]
            result = {
                "columns": [c[0] for c in cursor.description or []],
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "scalar": next(iter(rows[0].values())) if rows else None,
            }
            if len(json.dumps(result).encode()) > 2_000_000:
                raise ValueError("Query result exceeds 2 MB limit")
            return result
        finally:
            cursor.close()
            self.connection.set_progress_handler(None, 0)

    async def close(self):
        if getattr(self, "connection", None):
            self.connection.close()
            self.connection = None
