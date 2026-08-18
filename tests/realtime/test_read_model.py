from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.realtime.read_model import ConfirmedReadModelError, open_confirmed_repository
from src.storage import SQLiteStore


class ReadModelTests(unittest.TestCase):
    def test_existing_read_model_is_opened_without_migration_or_write_permission(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            database = root / "confirmed.sqlite"
            store = SQLiteStore(database, allowed_root=root).initialize()
            store.close()
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            with open_confirmed_repository(database) as repository:
                self.assertEqual(repository.count("regime_snapshot"), 0)
                with self.assertRaises(sqlite3.OperationalError):
                    repository.put_regime_snapshot(
                        "should-not-write",
                        {"state": "normal"},
                        signal_date="2024-01-03",
                        execution_date="2024-01-04",
                        as_of="2024-01-03T22:00:00Z",
                        strategy_version="test",
                        state="normal",
                        quality="OK",
                    )
            after = hashlib.sha256(database.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_missing_or_incompatible_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw_root:
            path = Path(raw_root) / "missing.sqlite"
            with self.assertRaises(ConfirmedReadModelError):
                with open_confirmed_repository(path):
                    pass


if __name__ == "__main__":
    unittest.main()
