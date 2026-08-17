import unittest
from datetime import datetime, timezone

from src.storage import (
    JsonDataSourceAdapter,
    MarketDataRequest,
    PriceFieldMapping,
    RawSnapshot,
    RawSnapshotManifest,
    SourceResponse,
    SUPPORTED_SYMBOLS,
    DataContractError,
    DuplicateSnapshotError,
    map_price_record,
)


class DataSourceIntegrationTests(unittest.TestCase):
    def _request(self, *, symbols=("QQQ",), price_basis="adjusted_ohlcv", **kwargs):
        values = {
            "start_date": "2020-03-16",
            "end_date": "2020-03-17",
        }
        values.update(kwargs)
        return MarketDataRequest(
            source="fixture-provider",
            symbols=symbols,
            price_basis=price_basis,
            **values,
        )

    def test_supported_universe_and_price_basis_are_explicit(self) -> None:
        self.assertEqual(
            set(SUPPORTED_SYMBOLS),
            {"QQQ", "QLD", "VOO", "SPY", "BIL", "TLT", "IAU", "XLU", "SVXY", "VIX", "VIX3M"},
        )
        request = self._request(symbols=("QLD", "QQQ"))
        self.assertEqual(request.symbols, ("QLD", "QQQ"))
        self.assertEqual(request.request_id, self._request(symbols=("QQQ", "QLD")).request_id)

        index_request = self._request(symbols=("VIX",), price_basis="index_level")
        self.assertEqual(index_request.price_basis, "index_level")

        with self.assertRaisesRegex(DataContractError, "cannot mix"):
            self._request(symbols=("QQQ", "VIX"), price_basis="adjusted_ohlcv")
        with self.assertRaisesRegex(DataContractError, "unsupported symbols"):
            self._request(symbols=("NOT_A_SYMBOL",))
        with self.assertRaisesRegex(DataContractError, "credentials"):
            self._request(provider_params={"api_key": "must-not-be-here"})

    def test_provider_field_mapping_preserves_values_without_adjustment(self) -> None:
        mapping = PriceFieldMapping(
            symbol="ticker",
            bar_date="tradingDate",
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )
        row = {"ticker": "qqq", "tradingDate": "2020-03-16", "o": "190.1", "h": "191.2", "l": "187.3", "c": "188.4", "v": 123}
        mapped = map_price_record(row, mapping=mapping, expected_symbol="QQQ")
        self.assertEqual(mapped["symbol"], "QQQ")
        self.assertEqual(mapped["bar_date"], "2020-03-16")
        self.assertEqual(mapped["close"], "188.4")
        self.assertEqual(mapped["volume"], 123)

        with self.assertRaisesRegex(DataContractError, "missing"):
            map_price_record({"ticker": "QQQ", "tradingDate": "2020-03-16"}, mapping=mapping)

    def test_successful_fetch_captures_provenance_and_is_repeatable(self) -> None:
        request = self._request()
        calls = []

        def transport(received):
            calls.append(received.request_id)
            return SourceResponse(
                status_code=200,
                payload={"bars": [{"date": "2020-03-16", "close": 188.4}]},
                retrieved_at="2026-08-17T16:00:00+08:00",
                provider_request_id="fixture-req-1",
            )

        adapter = JsonDataSourceAdapter(
            "fixture-provider",
            transport,
            clock=lambda: datetime(2026, 8, 17, 8, 1, tzinfo=timezone.utc),
        )
        first = adapter.fetch(request)
        second = adapter.fetch(request)

        self.assertEqual(calls, [request.request_id, request.request_id])
        self.assertEqual(first.status, "success")
        self.assertEqual(first.quality, "OK")
        self.assertEqual(first.retrieved_at, "2026-08-17T08:00:00Z")
        self.assertEqual(first.payload["bars"][0]["close"], 188.4)
        self.assertEqual(first.payload_sha256, second.payload_sha256)
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.as_record()["source"], "fixture-provider")

    def test_failed_transport_never_looks_like_success_and_does_not_leak_error(self) -> None:
        request = self._request()

        def transport(_):
            raise RuntimeError("api_key=private-value should not be persisted")

        adapter = JsonDataSourceAdapter(
            "fixture-provider",
            transport,
            clock=lambda: "2026-08-17T08:00:00Z",
        )
        snapshot = adapter.fetch(request)
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.quality, "FAILED")
        self.assertEqual(snapshot.error_code, "transport_error")
        self.assertNotIn("private-value", snapshot.error_message)
        self.assertIsNone(snapshot.payload)

    def test_rate_limit_partial_and_missing_payload_are_explicit_failures(self) -> None:
        request = self._request()

        responses = [
            (SourceResponse(429, {"message": "slow down"}), "failed", "rate_limited"),
            (SourceResponse(206, {"bars": [{"date": "2020-03-16"}]}), "partial", None),
            (SourceResponse(200, []), "failed", "empty_payload"),
        ]
        for response, expected_status, expected_error in responses:
            with self.subTest(status_code=response.status_code):
                adapter = JsonDataSourceAdapter(
                    "fixture-provider",
                    lambda _, response=response: response,
                    clock=lambda: "2026-08-17T08:00:00Z",
                )
                snapshot = adapter.fetch(request)
                self.assertEqual(snapshot.status, expected_status)
                self.assertEqual(snapshot.error_code, expected_error)
                if expected_status == "partial":
                    self.assertEqual(snapshot.quality, "PARTIAL")

    def test_raw_snapshots_are_immutable_and_manifest_is_append_only(self) -> None:
        request = self._request()
        payload = {"bars": [{"date": "2020-03-16", "close": 188.4}]}
        first = RawSnapshot.capture(
            source="fixture-provider",
            request=request,
            retrieved_at="2026-08-17T08:00:00Z",
            payload=payload,
        )
        second = RawSnapshot.capture(
            source="fixture-provider",
            request=self._request(start_date="2020-03-17", end_date="2020-03-17"),
            retrieved_at="2026-08-17T08:00:01Z",
            payload={"bars": [{"date": "2020-03-17", "close": 190.0}]},
        )
        original_payload = first.payload
        original_payload["bars"][0]["close"] = 0
        self.assertEqual(first.payload["bars"][0]["close"], 188.4)

        manifest_a = RawSnapshotManifest.from_snapshots([second, first], created_at="2026-08-17T08:01:00Z")
        manifest_b = RawSnapshotManifest.from_snapshots([first, second], created_at="2026-08-17T08:01:00Z")
        self.assertEqual(manifest_a.as_dict(), manifest_b.as_dict())
        self.assertEqual(manifest_a.manifest_hash, manifest_b.manifest_hash)
        self.assertEqual(manifest_a.as_dict()["snapshot_count"], 2)

        with self.assertRaises(DuplicateSnapshotError):
            manifest_a.append(first)
        self.assertEqual(len(manifest_a.snapshots), 2)

        third = RawSnapshot.capture(
            source="fixture-provider",
            request=self._request(start_date="2020-03-18", end_date="2020-03-18"),
            retrieved_at="2026-08-17T08:00:02Z",
            payload={"bars": [{"date": "2020-03-18", "close": 191.0}]},
        )
        appended = manifest_a.append(third)
        self.assertEqual(len(appended.snapshots), 3)
        self.assertEqual(len(manifest_a.snapshots), 2)

    def test_changed_retrieval_metadata_creates_new_snapshot_instead_of_overwrite(self) -> None:
        request = self._request()
        common = {
            "source": "fixture-provider",
            "request": request,
            "payload": {"bars": [{"date": "2020-03-16", "close": 188.4}]},
        }
        first = RawSnapshot.capture(retrieved_at="2026-08-17T08:00:00Z", **common)
        revised = RawSnapshot.capture(retrieved_at="2026-08-17T08:05:00Z", **common)
        self.assertNotEqual(first.snapshot_id, revised.snapshot_id)
        manifest = RawSnapshotManifest.from_snapshots([first, revised], created_at="2026-08-17T08:06:00Z")
        self.assertEqual(len(manifest.snapshots), 2)

    def test_manifest_contains_failure_provenance(self) -> None:
        request = self._request()
        failed = RawSnapshot.failed(
            source="fixture-provider",
            request=request,
            retrieved_at="2026-08-17T08:00:00Z",
            error_code="rate_limited",
            error_message="provider rate limit response",
        )
        manifest = RawSnapshotManifest.from_snapshots([failed], created_at="2026-08-17T08:01:00Z")
        entry = manifest.as_dict()["snapshots"][0]
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["quality"], "FAILED")
        self.assertEqual(entry["error_code"], "rate_limited")
        self.assertIsNone(entry["payload_sha256"])


if __name__ == "__main__":
    unittest.main()
