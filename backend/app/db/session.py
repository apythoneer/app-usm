"""
Database session management — pyodbc connection pool for SQL Server.
FastAPI uses run_in_threadpool to keep async handlers non-blocking.
"""

import logging
import pyodbc
from contextlib import contextmanager
from typing import Generator, Optional

from app.core.config import get_settings
from app.services.keepass import get_sql_credentials

logger = logging.getLogger("usm.db")
settings = get_settings()

# Module-level connection pool (pyodbc doesn't have a built-in pool;
# we keep a single shared connection with reconnect on failure)
_pool: list[pyodbc.Connection] = []
_POOL_SIZE = 5


def _build_conn_str(username: str, password: str) -> str:
    import pyodbc as _pyodbc
    preferred = [settings.sql_driver, "ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
    available = _pyodbc.drivers()
    driver = next((d for d in preferred if d in available), preferred[-1])
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={settings.sql_server};"
        f"DATABASE={settings.sql_database};"
        f"UID={username};PWD={password};"
        "TrustServerCertificate=yes;Connection Timeout=30;"
    )


def get_connection() -> pyodbc.Connection:
    """Get a new pyodbc connection (credentials from KeePass)."""
    creds = get_sql_credentials()
    conn_str = _build_conn_str(creds["username"], creds["password"])
    conn = pyodbc.connect(conn_str, autocommit=False)
    conn.timeout = 30
    return conn


@contextmanager
def get_db_cursor() -> Generator:
    """
    Context manager yielding a cursor with auto commit/rollback.
    Use inside sync functions called via run_in_threadpool.
    """
    conn: Optional[pyodbc.Connection] = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def row_to_dict(cursor: pyodbc.Cursor, row) -> Optional[dict]:
    if row is None:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def rows_to_dicts(cursor: pyodbc.Cursor, rows) -> list[dict]:
    if not rows:
        return []
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


def test_connection() -> bool:
    """Health check — returns True if DB is reachable."""
    try:
        with get_db_cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False
