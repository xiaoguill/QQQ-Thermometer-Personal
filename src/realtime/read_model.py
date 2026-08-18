"""Thread-local, strictly read-only access to the existing M10 SQLite read model."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from urllib.parse import quote
from typing import Iterator

from src.storage import SQLiteRepository, SQLiteStore


class ConfirmedReadModelError(RuntimeError):
    """The configured confirmed read model cannot be opened read-only."""


def _read_only_uri(path: Path) -> str:
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"


@contextmanager
def open_confirmed_repository(path: str | Path) -> Iterator[SQLiteRepository]:
    """Open an existing M10 SQLite file without migration or write permission."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ConfirmedReadModelError("confirmed read model file does not exist")
    store = SQLiteStore(resolved, allowed_root=resolved.parent)
    try:
        connection = sqlite3.connect(
            _read_only_uri(resolved),
            uri=True,
            isolation_level=None,
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        store._connection = connection  # M16 adapter: validated read-only connection, never initialized via migration.
        store._verify_migration_rows(connection)
        store._verify_metadata(connection)
        store._initialized = True
    except Exception as exc:
        store.close()
        raise ConfirmedReadModelError("confirmed read model is not a compatible SQLite read model") from exc
    try:
        yield SQLiteRepository(store)
    finally:
        store.close()


__all__ = ["ConfirmedReadModelError", "open_confirmed_repository"]
