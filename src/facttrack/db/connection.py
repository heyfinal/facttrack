"""Thin psycopg2 connection helper. One pool per process, lazy init."""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

from facttrack.config import DB

_POOL: SimpleConnectionPool | None = None


def _ensure_pool() -> SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = SimpleConnectionPool(
            minconn=1,
            maxconn=8,
            dsn=DB.dsn,
        )
    return _POOL


@contextlib.contextmanager
def conn() -> Iterator[psycopg2.extensions.connection]:
    pool = _ensure_pool()
    c = pool.getconn()
    try:
        c.autocommit = False
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        pool.putconn(c)


@contextlib.contextmanager
def cursor(dict_rows: bool = True) -> Iterator[psycopg2.extensions.cursor]:
    cursor_factory = psycopg2.extras.RealDictCursor if dict_rows else None
    with conn() as c:
        cur = c.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


def ping() -> bool:
    """Quick health check — returns True if the database is reachable."""
    try:
        with cursor(dict_rows=False) as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1
    except Exception:
        return False
