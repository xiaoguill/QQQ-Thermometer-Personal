from __future__ import annotations

import unittest

from src.jobs.m18.read_model import FullChainSnapshot
from src.storage.m18.read_model_store import M18ReadModelStore
from src.storage.sqlite_store import SQLiteRepository, SQLiteStore
from tests.jobs.m18.test_read_model_contract import M18ReadModelContractTests


class M18ReadModelStoreTests(unittest.TestCase):
    def test_put_is_append_only_and_idempotent(self) -> None:
        fixture = M18ReadModelContractTests("test_round_trip_preserves_confirmed_targets_and_separates_provisional")
        fixture.setUp()
        snapshot = fixture._snapshot()
        store = SQLiteStore(":memory:").initialize()
        repository = SQLiteRepository(store).initialize()
        read_model = M18ReadModelStore(repository)

        first = read_model.put(snapshot)
        second = read_model.put(FullChainSnapshot.from_dict(snapshot.as_dict()))

        self.assertEqual(first.record_key, "m18|run-healthy")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(repository.count("run"), 1)
        self.assertEqual(read_model.latest().content_hash, snapshot.content_hash)
        self.assertEqual(read_model.get("run-healthy").run_id, "run-healthy")


if __name__ == "__main__":
    unittest.main()
