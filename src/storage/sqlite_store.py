"""Local SQLite storage boundary for the personal QQQ Thermometer.

This module deliberately owns persistence mechanics only.  It does not fetch
data, calculate indicators, evaluate regimes, or decide target weights.
Records are JSON payloads with indexed provenance fields, and every write is
append-only at the logical record-key level.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


STORAGE_SCHEMA = "qqq-storage-schema/v1"
STORAGE_IMPLEMENTATION_VERSION = "m08-sqlite-storage/v1"
STORAGE_SCHEMA_VERSION = 1

_DATE_FIELDS = {
    "bar_date",
    "event_date",
    "execution_date",
    "signal_date",
}
_TIMESTAMP_FIELDS = {
    "as_of",
    "approved_at",
    "finished_at",
    "started_at",
}
_QUALITY_VALUES = {"OK", "STALE", "PARTIAL", "FAILED", "NEEDS_REVIEW"}
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_NETWORK_PREFIXES = ("http://", "https://", "file:", "\\\\", "//")


class StorageError(RuntimeError):
    """Base error for the local storage boundary."""


class StorageValidationError(StorageError, ValueError):
    """Raised when a record or storage path violates the contract."""


class StorageSchemaError(StorageError):
    """Raised when a database schema is unknown, corrupt, or not forward-safe."""


class StorageConflictError(StorageError):
    """Raised when an immutable key is reused with different content."""


class StorageImmutableError(StorageError):
    """Reserved for callers that attempt a destructive record operation."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StorageValidationError("payload must be finite, JSON-serializable data") from exc


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalise_key_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _assert_safe_key(key: str) -> None:
    normalised = _normalise_key_name(key)
    if any(fragment in normalised for fragment in _SENSITIVE_KEY_FRAGMENTS):
        raise StorageValidationError(f"sensitive field is not allowed in storage payload: {key}")


def _assert_json_safe(value: Any, *, field_name: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or not key.strip():
                raise StorageValidationError(f"{field_name} object keys must be non-empty strings")
            _assert_safe_key(key)
            _assert_json_safe(nested, field_name=f"{field_name}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_json_safe(nested, field_name=f"{field_name}[{index}]")
        return
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise StorageValidationError(f"{field_name} contains a non-finite number")
        return
    raise StorageValidationError(f"{field_name} contains unsupported value type: {type(value).__name__}")


def _payload_from(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        value = value.as_dict()
    if not isinstance(value, Mapping):
        raise StorageValidationError("payload must be a mapping or expose as_dict()")
    payload = copy.deepcopy(dict(value))
    _assert_json_safe(payload)
    _canonical_json(payload)
    return payload


def _validate_record_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageValidationError("record_key must be a non-empty string")
    return value.strip()


def _validate_iso_date(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StorageValidationError(f"{field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise StorageValidationError(f"{field_name} must be an ISO date") from exc
    return parsed.isoformat()


def _validate_timestamp(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise StorageValidationError(f"{field_name} must be an ISO timestamp")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise StorageValidationError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise StorageValidationError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_metadata(metadata: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    unknown = sorted(set(metadata) - set(allowed))
    if unknown:
        raise StorageValidationError(f"unsupported metadata fields: {unknown}")
    result: dict[str, Any] = {}
    for field_name in allowed:
        if field_name not in metadata:
            continue
        value = metadata[field_name]
        if field_name in _DATE_FIELDS:
            result[field_name] = _validate_iso_date(value, field_name)
        elif field_name in _TIMESTAMP_FIELDS:
            result[field_name] = _validate_timestamp(value, field_name)
        elif field_name in {"nav", "cash", "price", "quantity", "cost"}:
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise StorageValidationError(f"{field_name} must be a finite number")
                result[field_name] = float(value)
            else:
                result[field_name] = None
        elif value is None:
            result[field_name] = None
        else:
            if not isinstance(value, str) or not value.strip():
                raise StorageValidationError(f"{field_name} must be a non-empty string")
            result[field_name] = value.strip()
    return result


@dataclass(frozen=True)
class Migration:
    version: int
    migration_id: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256("\n".join(self.statements).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredRecord:
    """An immutable record returned by a repository read."""

    entity: str
    record_key: str
    content_hash: str
    payload: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "record_key": self.record_key,
            "content_hash": self.content_hash,
            "payload": copy.deepcopy(dict(self.payload)),
            "metadata": copy.deepcopy(dict(self.metadata)),
        }


@dataclass(frozen=True)
class _TableSpec:
    table: str
    columns: tuple[str, ...]
    idempotency_column: str | None = None


_TABLES: dict[str, _TableSpec] = {
    "market_snapshot": _TableSpec(
        "market_snapshots",
        ("snapshot_kind", "symbol", "bar_date", "as_of", "source", "price_basis", "quality"),
    ),
    "indicator_snapshot": _TableSpec(
        "indicator_snapshots",
        ("signal_date", "as_of", "indicator_version", "quality"),
    ),
    "regime_snapshot": _TableSpec(
        "regime_snapshots",
        ("signal_date", "execution_date", "as_of", "strategy_version", "state", "quality"),
    ),
    "target_weight_snapshot": _TableSpec(
        "target_weight_snapshots",
        ("signal_date", "execution_date", "as_of", "strategy_version", "state", "weight_status", "data_quality"),
    ),
    "portfolio_snapshot": _TableSpec(
        "portfolio_snapshots",
        ("portfolio_id", "as_of", "status", "nav", "cash"),
    ),
    "run": _TableSpec(
        "run_records",
        ("run_type", "started_at", "finished_at", "status", "strategy_version", "data_version"),
    ),
    "quality_event": _TableSpec(
        "quality_events",
        ("event_date", "source", "symbol", "severity", "status"),
    ),
    "strategy_version": _TableSpec(
        "strategy_versions",
        ("version", "status", "config_hash", "approved_by", "approved_at"),
    ),
    "ledger_event": _TableSpec(
        "ledger_events",
        ("event_date", "event_type", "idempotency_key", "status", "quantity", "price", "cost"),
        idempotency_column="idempotency_key",
    ),
}


_MIGRATION_1_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS storage_metadata (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_name TEXT NOT NULL, schema_version INTEGER NOT NULL, implementation_version TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, migration_id TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS market_snapshots (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), snapshot_kind TEXT NOT NULL, symbol TEXT, bar_date TEXT, as_of TEXT, source TEXT, price_basis TEXT, quality TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS indicator_snapshots (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), signal_date TEXT, as_of TEXT, indicator_version TEXT, quality TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS regime_snapshots (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), signal_date TEXT, execution_date TEXT, as_of TEXT, strategy_version TEXT, state TEXT, quality TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS target_weight_snapshots (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), signal_date TEXT, execution_date TEXT, as_of TEXT, strategy_version TEXT, state TEXT, weight_status TEXT, data_quality TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS portfolio_snapshots (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), portfolio_id TEXT, as_of TEXT, status TEXT, nav REAL, cash REAL, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS run_records (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), run_type TEXT, started_at TEXT, finished_at TEXT, status TEXT, strategy_version TEXT, data_version TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS quality_events (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), event_date TEXT, source TEXT, symbol TEXT, severity TEXT, status TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS strategy_versions (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), version TEXT, status TEXT, config_hash TEXT, approved_by TEXT, approved_at TEXT, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ledger_events (record_key TEXT PRIMARY KEY CHECK (length(record_key) > 0), event_date TEXT, event_type TEXT, idempotency_key TEXT NOT NULL UNIQUE, status TEXT, quantity REAL, price REAL, cost REAL, content_hash TEXT NOT NULL CHECK (length(content_hash) = 64), payload_json TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshots_date ON market_snapshots (symbol, bar_date)",
    "CREATE INDEX IF NOT EXISTS idx_indicator_snapshots_date ON indicator_snapshots (signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_regime_snapshots_date ON regime_snapshots (signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_target_weight_snapshots_date ON target_weight_snapshots (signal_date)",
    "CREATE INDEX IF NOT EXISTS idx_run_records_status ON run_records (status, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_quality_events_date ON quality_events (event_date, severity)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_events_date ON ledger_events (event_date)",
)

MIGRATIONS = (
    Migration(
        version=1,
        migration_id="m08-initial-storage-schema",
        statements=_MIGRATION_1_STATEMENTS,
    ),
)


class SQLiteStore:
    """Explicitly opened, explicitly initialized local SQLite store."""

    def __init__(self, path: str | Path, *, allowed_root: str | Path | None = None) -> None:
        if isinstance(path, Path):
            raw_path = str(path)
        elif isinstance(path, str):
            raw_path = path.strip()
        else:
            raise StorageValidationError("database path must be a string or pathlib.Path")
        if not raw_path:
            raise StorageValidationError("database path must not be empty")
        if raw_path != ":memory:" and raw_path.lower().startswith(_NETWORK_PREFIXES):
            raise StorageValidationError("only a local SQLite path or :memory: is allowed")
        self._raw_path = raw_path
        self._path = None if raw_path == ":memory:" else Path(raw_path).expanduser().resolve()
        self._allowed_root = None if allowed_root is None else Path(allowed_root).expanduser().resolve()
        if self._path is not None and self._allowed_root is not None:
            try:
                self._path.relative_to(self._allowed_root)
            except ValueError as exc:
                raise StorageValidationError("database path is outside allowed_root") from exc
        self._connection: sqlite3.Connection | None = None
        self._initialized = False

    @property
    def path(self) -> str:
        return self._raw_path if self._path is None else str(self._path)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("SQLiteStore is not open; call connect() or use it as a context manager")
        return self._connection

    @property
    def initialized(self) -> bool:
        return self._initialized

    def connect(self) -> "SQLiteStore":
        if self._connection is not None:
            return self
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self._raw_path,
            isolation_level=None,
            check_same_thread=True,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA synchronous = FULL")
        return self

    def initialize(self) -> "SQLiteStore":
        self.connect()
        connection = self.connection
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        highest = MIGRATIONS[-1].version
        if current > highest:
            raise StorageSchemaError(f"database schema {current} is newer than supported schema {highest}")
        if current < 0:
            raise StorageSchemaError("database schema version cannot be negative")
        try:
            with self.transaction():
                for migration in MIGRATIONS:
                    if migration.version <= current:
                        continue
                    if migration.version != current + 1:
                        raise StorageSchemaError("storage migrations contain a version gap")
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations (version, migration_id, checksum) VALUES (?, ?, ?)",
                        (migration.version, migration.migration_id, migration.checksum),
                    )
                    connection.execute(
                        "INSERT INTO storage_metadata (singleton, schema_name, schema_version, implementation_version) VALUES (?, ?, ?, ?)",
                        (1, STORAGE_SCHEMA, migration.version, STORAGE_IMPLEMENTATION_VERSION),
                    )
                    connection.execute(f"PRAGMA user_version = {migration.version}")
                    current = migration.version
                self._verify_migration_rows(connection)
                self._verify_metadata(connection)
        except sqlite3.DatabaseError as exc:
            raise StorageSchemaError("storage initialization failed and was rolled back") from exc
        self._initialized = True
        return self

    def _verify_migration_rows(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "schema_migrations"):
            raise StorageSchemaError("schema_migrations table is missing")
        rows = connection.execute(
            "SELECT version, migration_id, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        expected = {migration.version: migration for migration in MIGRATIONS}
        if len(rows) != len(expected):
            raise StorageSchemaError("schema migration history is incomplete")
        for row in rows:
            migration = expected.get(int(row["version"]))
            if migration is None or row["migration_id"] != migration.migration_id or row["checksum"] != migration.checksum:
                raise StorageSchemaError("schema migration history is not trusted")

    def _verify_metadata(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "storage_metadata"):
            raise StorageSchemaError("storage_metadata table is missing")
        row = connection.execute(
            "SELECT schema_name, schema_version, implementation_version FROM storage_metadata WHERE singleton = ?",
            (1,),
        ).fetchone()
        if row is None or row["schema_name"] != STORAGE_SCHEMA or int(row["schema_version"]) != STORAGE_SCHEMA_VERSION:
            raise StorageSchemaError("storage metadata is missing or inconsistent")

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?",
            ("table", name),
        ).fetchone() is not None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        owned = not connection.in_transaction
        if owned:
            connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            if owned and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        else:
            if owned and connection.in_transaction:
                connection.execute("COMMIT")

    @property
    def schema_version(self) -> int:
        self._require_initialized()
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def schema_manifest(self) -> dict[str, Any]:
        self._require_initialized()
        tables = self.connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = ? AND name NOT LIKE ? ORDER BY name",
            ("table", "sqlite_%"),
        ).fetchall()
        return {
            "schema": STORAGE_SCHEMA,
            "schema_version": self.schema_version,
            "implementation_version": STORAGE_IMPLEMENTATION_VERSION,
            "tables": [{"name": row["name"], "sql": row["sql"]} for row in tables],
            "migrations": [
                {"version": item.version, "migration_id": item.migration_id, "checksum": item.checksum}
                for item in MIGRATIONS
            ],
        }

    def backup_to(self, destination: str | Path) -> Path:
        self._require_initialized()
        if isinstance(destination, Path):
            target = destination.expanduser().resolve()
        elif isinstance(destination, str) and destination.strip():
            raw = destination.strip()
            if raw.lower().startswith(_NETWORK_PREFIXES) or raw == ":memory:":
                raise StorageValidationError("backup destination must be a local file path")
            target = Path(raw).expanduser().resolve()
        else:
            raise StorageValidationError("backup destination must be a local file path")
        if target == self._path:
            raise StorageValidationError("backup destination must differ from the source database")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_connection = sqlite3.connect(str(target), isolation_level=None, check_same_thread=True)
        try:
            self.connection.backup(backup_connection)
        finally:
            backup_connection.close()
        return target

    def _require_initialized(self) -> None:
        if self._connection is None or not self._initialized:
            raise StorageError("SQLiteStore must be open and initialized before use")

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._initialized = False

    def __enter__(self) -> "SQLiteStore":
        return self.connect()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


class SQLiteRepository:
    """Typed façade over append-only JSON repositories."""

    def __init__(self, store: SQLiteStore) -> None:
        if not isinstance(store, SQLiteStore):
            raise StorageValidationError("SQLiteRepository requires SQLiteStore")
        self.store = store

    def initialize(self) -> "SQLiteRepository":
        self.store.initialize()
        return self

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.store.transaction() as connection:
            yield connection

    def put(self, entity: str, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        spec = _TABLES.get(entity)
        if spec is None:
            raise StorageValidationError(f"unsupported storage entity: {entity}")
        self.store._require_initialized()
        key = _validate_record_key(record_key)
        body = _payload_from(payload)
        content_hash = _content_hash(body)
        values = _validate_metadata(metadata, spec.columns)
        values = {column: values.get(column) for column in spec.columns}
        if entity == "ledger_event" and not values.get("idempotency_key"):
            raise StorageValidationError("ledger_event requires idempotency_key")
        if entity == "strategy_version" and not values.get("version"):
            raise StorageValidationError("strategy_version requires version")
        row = self._get_row(entity, key)
        if row is not None:
            return self._same_or_conflict(entity, key, row, content_hash)
        if spec.idempotency_column:
            existing = self._get_row_by_column(entity, spec.idempotency_column, values[spec.idempotency_column])
            if existing is not None:
                return self._same_or_conflict(entity, key, existing, content_hash, idempotency=True)

        columns = ("record_key",) + spec.columns + ("content_hash", "payload_json")
        row_values = (key,) + tuple(values[column] for column in spec.columns) + (content_hash, _canonical_json(body))
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        try:
            with self.store.transaction():
                self.store.connection.execute(
                    f"INSERT INTO {spec.table} ({column_sql}) VALUES ({placeholders})",
                    row_values,
                )
        except sqlite3.IntegrityError as exc:
            retry = self._get_row(entity, key)
            if retry is not None:
                return self._same_or_conflict(entity, key, retry, content_hash)
            if spec.idempotency_column:
                retry = self._get_row_by_column(entity, spec.idempotency_column, values[spec.idempotency_column])
                if retry is not None:
                    return self._same_or_conflict(entity, key, retry, content_hash, idempotency=True)
            raise StorageConflictError(f"immutable write rejected for {entity}:{key}") from exc
        return self._row_to_record(entity, self._get_row(entity, key))

    def _same_or_conflict(
        self,
        entity: str,
        key: str,
        row: sqlite3.Row,
        content_hash: str,
        *,
        idempotency: bool = False,
    ) -> StoredRecord:
        if row["content_hash"] == content_hash:
            return self._row_to_record(entity, row)
        label = "idempotency key" if idempotency else "record key"
        raise StorageConflictError(f"{label} already contains different content for {entity}:{key}")

    def _get_row(self, entity: str, record_key: str) -> sqlite3.Row | None:
        spec = _TABLES[entity]
        return self.store.connection.execute(
            f"SELECT * FROM {spec.table} WHERE record_key = ?",
            (record_key,),
        ).fetchone()

    def _get_row_by_column(self, entity: str, column: str, value: Any) -> sqlite3.Row | None:
        spec = _TABLES[entity]
        if column != spec.idempotency_column:
            raise StorageValidationError("invalid idempotency lookup")
        return self.store.connection.execute(
            f"SELECT * FROM {spec.table} WHERE {column} = ?",
            (value,),
        ).fetchone()

    def _row_to_record(self, entity: str, row: sqlite3.Row | None) -> StoredRecord:
        if row is None:
            raise StorageError("record disappeared during a successful write")
        metadata = {
            key: row[key]
            for key in row.keys()
            if key not in {"record_key", "content_hash", "payload_json"}
        }
        return StoredRecord(
            entity=entity,
            record_key=str(row["record_key"]),
            content_hash=str(row["content_hash"]),
            payload=json.loads(str(row["payload_json"])),
            metadata=metadata,
        )

    def get(self, entity: str, record_key: str) -> StoredRecord | None:
        self._validate_entity(entity)
        self.store._require_initialized()
        row = self._get_row(entity, _validate_record_key(record_key))
        return None if row is None else self._row_to_record(entity, row)

    def list(self, entity: str, *, limit: int = 100, offset: int = 0) -> tuple[StoredRecord, ...]:
        self._validate_entity(entity)
        self.store._require_initialized()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise StorageValidationError("limit must be an integer between 1 and 1000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise StorageValidationError("offset must be a non-negative integer")
        spec = _TABLES[entity]
        rows = self.store.connection.execute(
            f"SELECT * FROM {spec.table} ORDER BY record_key LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return tuple(self._row_to_record(entity, row) for row in rows)

    def count(self, entity: str) -> int:
        self._validate_entity(entity)
        self.store._require_initialized()
        spec = _TABLES[entity]
        return int(self.store.connection.execute(f"SELECT COUNT(*) FROM {spec.table}").fetchone()[0])

    def _validate_entity(self, entity: str) -> None:
        if entity not in _TABLES:
            raise StorageValidationError(f"unsupported storage entity: {entity}")

    def put_market_snapshot(self, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("market_snapshot", record_key, payload, **metadata)

    def put_indicator_snapshot(self, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("indicator_snapshot", record_key, payload, **metadata)

    def put_regime_snapshot(self, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("regime_snapshot", record_key, payload, **metadata)

    def put_target_weight_snapshot(self, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("target_weight_snapshot", record_key, payload, **metadata)

    def put_portfolio_snapshot(self, record_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("portfolio_snapshot", record_key, payload, **metadata)

    def put_run(self, run_id: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("run", run_id, payload, **metadata)

    def put_quality_event(self, event_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        return self.put("quality_event", event_key, payload, **metadata)

    def put_strategy_version(self, version: str, payload: Any, **metadata: Any) -> StoredRecord:
        values = dict(metadata)
        values.setdefault("version", version)
        return self.put("strategy_version", version, payload, **values)

    def put_ledger_event(self, event_key: str, payload: Any, **metadata: Any) -> StoredRecord:
        values = dict(metadata)
        values.setdefault("idempotency_key", event_key)
        return self.put("ledger_event", event_key, payload, **values)

    def delete(self, entity: str, record_key: str) -> None:
        self._validate_entity(entity)
        raise StorageImmutableError(f"historical records are append-only; delete is forbidden for {entity}:{record_key}")

    def update(self, entity: str, record_key: str, payload: Any) -> None:
        self._validate_entity(entity)
        raise StorageImmutableError(f"historical records are append-only; update is forbidden for {entity}:{record_key}")


__all__ = [
    "MIGRATIONS",
    "STORAGE_IMPLEMENTATION_VERSION",
    "STORAGE_SCHEMA",
    "STORAGE_SCHEMA_VERSION",
    "Migration",
    "SQLiteRepository",
    "SQLiteStore",
    "StorageConflictError",
    "StorageError",
    "StorageImmutableError",
    "StorageSchemaError",
    "StorageValidationError",
    "StoredRecord",
]
