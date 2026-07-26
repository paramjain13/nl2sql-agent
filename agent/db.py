"""SQLite helpers: schema extraction + safe, read-only execution."""
import re
import sqlite3
from typing import Any, List, Tuple

import config

# Block anything that can mutate the DB. Executed SQL must be read-only.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|pragma)\b",
    re.IGNORECASE,
)


def get_full_schema(db_path: str) -> str:
    """Return the CREATE TABLE statements for every table in the DB."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL;"
        )
        return "\n\n".join(row[0] for row in cur.fetchall())
    finally:
        conn.close()


def execute_sql(db_path: str, sql: str) -> Tuple[List[Tuple[Any, ...]], str]:
    """Run read-only SQL. Returns (rows, error). error='' on success.

    rows are returned as a list of tuples so they can be compared as sets in EX.
    """
    if _FORBIDDEN.search(sql):
        return [], "Refused: only read-only SELECT queries are allowed."

    conn = sqlite3.connect(db_path, timeout=config.SQL_TIMEOUT_SECONDS)
    try:
        conn.execute(f"PRAGMA busy_timeout = {config.SQL_TIMEOUT_SECONDS * 1000};")
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall(), ""
    except Exception as e:  # noqa: BLE001 - we want the message string
        return [], str(e)
    finally:
        conn.close()
