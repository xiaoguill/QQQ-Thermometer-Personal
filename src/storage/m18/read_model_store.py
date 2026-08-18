"""Append-only M18 projection storage over the existing M08 SQLite schema."""

from __future__ import annotations

from typing import Any

from src.jobs.m18.read_model import FullChainSnapshot, M18ReadModelError, M18_READ_MODEL_SCHEMA
from src.storage.sqlite_store import SQLiteRepository, StoredRecord


M18_RUN_TYPE = "m18_full_chain"
_KEY_PREFIX = "m18|"


class M18ReadModelStore:
    """Persist and read immutable M18 snapshots without changing M08 schema."""

    def __init__(self, repository: SQLiteRepository) -> None:
        if not isinstance(repository, SQLiteRepository):
            raise M18ReadModelError("M18ReadModelStore requires SQLiteRepository")
        if not repository.store.initialized:
            raise M18ReadModelError("M18ReadModelStore requires an initialized SQLite store")
        self.repository = repository

    @staticmethod
    def record_key(run_id: str) -> str:
        if not isinstance(run_id, str) or not run_id.strip() or "|" in run_id:
            raise M18ReadModelError("run_id must be non-empty and cannot contain |")
        return f"{_KEY_PREFIX}{run_id.strip()}"

    def put(self, snapshot: FullChainSnapshot) -> StoredRecord:
        if not isinstance(snapshot, FullChainSnapshot):
            raise M18ReadModelError("snapshot must be FullChainSnapshot")
        payload = snapshot.as_dict()
        return self.repository.put_run(
            self.record_key(snapshot.run_id),
            payload,
            run_type=M18_RUN_TYPE,
            started_at=snapshot.created_at,
            finished_at=snapshot.created_at,
            status=snapshot.run_status,
            strategy_version=snapshot.strategy_version or "unavailable",
            data_version=snapshot.data_version,
        )

    @staticmethod
    def _decode(record: StoredRecord) -> FullChainSnapshot:
        try:
            return FullChainSnapshot.from_dict(record.payload)
        except (M18ReadModelError, TypeError, ValueError) as exc:
            raise M18ReadModelError(f"invalid persisted M18 read model: {record.record_key}") from exc

    def get(self, run_id: str) -> FullChainSnapshot | None:
        record = self.repository.get("run", self.record_key(run_id))
        return None if record is None else self._decode(record)

    def recent(self, *, limit: int = 100) -> tuple[FullChainSnapshot, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise M18ReadModelError("limit must be between 1 and 1000")
        records = self.repository.list("run", limit=1000)
        snapshots: list[FullChainSnapshot] = []
        for record in records:
            if record.metadata.get("run_type") != M18_RUN_TYPE and record.payload.get("schema") != M18_READ_MODEL_SCHEMA:
                continue
            snapshots.append(self._decode(record))
        snapshots.sort(key=lambda item: (item.created_at, item.run_id))
        return tuple(snapshots[-limit:])

    def latest(self) -> FullChainSnapshot | None:
        values = self.recent(limit=1)
        return values[-1] if values else None

    def latest_payload(self) -> dict[str, Any] | None:
        snapshot = self.latest()
        return None if snapshot is None else snapshot.as_dict()


__all__ = ["M18_RUN_TYPE", "M18ReadModelError", "M18ReadModelStore"]
