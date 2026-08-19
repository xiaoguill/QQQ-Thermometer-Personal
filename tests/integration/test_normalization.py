import unittest
from datetime import timedelta

from src.storage import (
    ListingRegistry,
    MarketDataRequest,
    RawSnapshot,
    TradingCalendar,
    normalize_snapshots,
)


AS_OF = "2020-03-18T20:00:00Z"
RETRIEVED = AS_OF


class NormalizationIntegrationTests(unittest.TestCase):
    def _request(
        self,
        *,
        source="provider-a",
        symbols=("QQQ",),
        start_date="2020-03-16",
        end_date="2020-03-16",
        timezone="America/New_York",
        price_basis="adjusted_ohlcv",
    ):
        return MarketDataRequest(
            source=source,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
            price_basis=price_basis,
        )

    def _bar(self, day="2020-03-16", *, close=100.0, symbol="QQQ", **overrides):
        row = {
            "symbol": symbol,
            "date": day,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": close,
            "volume": 1000,
        }
        row.update(overrides)
        return row

    def _snapshot(
        self,
        bars,
        *,
        source="provider-a",
        request=None,
        retrieved_at=RETRIEVED,
        status="success",
        timezone_name=None,
        price_basis=None,
    ):
        request = request or self._request(source=source)
        if status == "failed":
            return RawSnapshot.failed(
                source=source,
                request=request,
                retrieved_at=retrieved_at,
                error_code="fixture_error",
                error_message="fixture provider failed",
            )
        return RawSnapshot.capture(
            source=source,
            request=request,
            retrieved_at=retrieved_at,
            payload={"bars": list(bars)},
            status=status,
            timezone_name=timezone_name,
            price_basis=price_basis,
        )

    def test_valid_bar_preserves_provenance_and_is_deterministic(self):
        snapshot = self._snapshot([self._bar()])
        first = normalize_snapshots([snapshot], as_of=AS_OF)
        second = normalize_snapshots([snapshot], as_of=AS_OF)

        self.assertEqual(first.quality, "OK")
        self.assertTrue(first.allows_confirmed)
        self.assertEqual(len(first.bars), 1)
        self.assertEqual(first.bars[0].source, "provider-a")
        self.assertEqual(first.bars[0].snapshot_ids, (snapshot.snapshot_id,))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.bars[0].close, 100.0)

    def test_calendar_rejects_weekends_and_known_nyse_holidays(self):
        calendar = TradingCalendar()
        self.assertFalse(calendar.is_trading_day("2020-03-15"))
        self.assertFalse(calendar.is_trading_day("2020-11-26"))
        self.assertTrue(calendar.is_trading_day("2020-11-27"))

    def test_vxx_has_an_explicit_first_available_date(self):
        self.assertEqual(ListingRegistry().first_date("VXX"), "2009-01-29")

    def test_missing_session_is_partial_and_never_filled(self):
        request = self._request(start_date="2020-03-16", end_date="2020-03-18")
        result = normalize_snapshots(
            [self._snapshot([self._bar("2020-03-16"), self._bar("2020-03-18")], request=request)],
            as_of=AS_OF,
        )

        self.assertEqual(result.quality, "PARTIAL")
        self.assertEqual([bar.bar_date for bar in result.bars], ["2020-03-16", "2020-03-18"])
        self.assertFalse(result.allows_confirmed)
        self.assertEqual({event.event_type for event in result.quality_events}, {"missing_bar"})

    def test_long_missing_window_requires_review(self):
        request = self._request(start_date="2020-03-16", end_date="2020-03-25")
        result = normalize_snapshots(
            [self._snapshot([self._bar("2020-03-16"), self._bar("2020-03-25")], request=request)],
            as_of="2020-03-25T20:00:00Z",
            max_missing_sessions=3,
        )

        self.assertEqual(result.quality, "NEEDS_REVIEW")
        event = next(event for event in result.quality_events if event.event_type == "missing_window")
        self.assertGreaterEqual(event.details["missing_sessions"], 3)
        self.assertFalse(result.allows_confirmed)

    def test_duplicate_bar_is_not_silently_deduplicated(self):
        result = normalize_snapshots(
            [self._snapshot([self._bar(), self._bar(close=101.0)])],
            as_of=AS_OF,
        )

        self.assertEqual(result.quality, "NEEDS_REVIEW")
        self.assertEqual(result.bars, ())
        self.assertTrue(any(event.event_type == "duplicate_bar" for event in result.quality_events))

    def test_non_trading_and_pre_listing_dates_are_rejected(self):
        request = self._request(start_date="2020-03-10", end_date="2020-03-16")
        result = normalize_snapshots(
            [self._snapshot([self._bar("2020-03-15"), self._bar("2020-03-10")], request=request)],
            as_of=AS_OF,
            listing_registry=ListingRegistry({"QQQ": "2020-03-16"}),
        )

        event_types = {event.event_type for event in result.quality_events}
        self.assertIn("non_trading_day", event_types)
        self.assertIn("pre_listing_date", event_types)
        self.assertEqual(result.bars, ())
        self.assertEqual(result.quality, "NEEDS_REVIEW")

    def test_abnormal_ohlc_is_rejected(self):
        result = normalize_snapshots(
            [self._snapshot([self._bar(high=98.0)])],
            as_of=AS_OF,
        )

        self.assertEqual(result.bars, ())
        self.assertTrue(any(event.event_type == "abnormal_price" for event in result.quality_events))
        self.assertEqual(result.quality, "NEEDS_REVIEW")

    def test_identical_cross_source_prices_merge_with_both_sources(self):
        request_a = self._request(source="provider-a")
        request_b = self._request(source="provider-b")
        result = normalize_snapshots(
            [
                self._snapshot([self._bar()], source="provider-a", request=request_a),
                self._snapshot([self._bar()], source="provider-b", request=request_b),
            ],
            as_of=AS_OF,
        )

        self.assertEqual(result.quality, "OK")
        self.assertEqual(result.bars[0].sources, ("provider-a", "provider-b"))
        self.assertEqual(len(result.bars[0].snapshot_ids), 2)

    def test_cross_source_price_difference_requires_review(self):
        request_a = self._request(source="provider-a")
        request_b = self._request(source="provider-b")
        result = normalize_snapshots(
            [
                self._snapshot([self._bar()], source="provider-a", request=request_a),
                self._snapshot([self._bar(close=101.0)], source="provider-b", request=request_b),
            ],
            as_of=AS_OF,
        )

        self.assertEqual(result.bars, ())
        self.assertEqual(result.quality, "NEEDS_REVIEW")
        self.assertTrue(any(event.event_type == "cross_source_price_conflict" for event in result.quality_events))

    def test_stale_snapshot_is_visible_and_cannot_confirm(self):
        snapshot = self._snapshot([self._bar()], retrieved_at="2020-03-10T20:00:00Z")
        result = normalize_snapshots([snapshot], as_of=AS_OF, max_staleness=timedelta(days=1))

        self.assertEqual(result.quality, "STALE")
        self.assertEqual(result.bars[0].quality, "STALE")
        self.assertFalse(result.allows_confirmed)
        self.assertTrue(any(event.event_type == "stale_snapshot" for event in result.quality_events))

    def test_failed_snapshot_is_not_success(self):
        snapshot = self._snapshot([], status="failed")
        result = normalize_snapshots([snapshot], as_of=AS_OF)

        self.assertEqual(result.quality, "FAILED")
        self.assertEqual(result.bars, ())
        self.assertFalse(result.allows_confirmed)
        self.assertTrue(any(event.event_type == "snapshot_failed" for event in result.quality_events))

    def test_adjustment_factor_is_not_applied_without_explicit_rule(self):
        result = normalize_snapshots(
            [self._snapshot([self._bar(adjustment_factor=0.5)])],
            as_of=AS_OF,
        )

        self.assertEqual(result.bars, ())
        self.assertEqual(result.quality, "NEEDS_REVIEW")
        self.assertTrue(any(event.event_type == "adjustment_metadata_conflict" for event in result.quality_events))

    def test_future_bar_and_timezone_mismatch_are_rejected(self):
        request = self._request(start_date="2020-03-18", end_date="2020-03-20")
        result = normalize_snapshots(
            [self._snapshot([self._bar("2020-03-19")], request=request)],
            as_of="2020-03-18T20:00:00Z",
        )
        self.assertTrue(any(event.event_type == "future_bar" for event in result.quality_events))
        self.assertFalse(result.allows_confirmed)

        mismatch = self._snapshot([self._bar()], timezone_name="UTC")
        mismatch_result = normalize_snapshots([mismatch], as_of=AS_OF)
        self.assertEqual(mismatch_result.bars, ())
        self.assertTrue(any(event.event_type == "snapshot_metadata_mismatch" for event in mismatch_result.quality_events))

    def test_partial_snapshot_and_missing_volume_are_explicit(self):
        snapshot = self._snapshot([self._bar(volume=None)], status="partial")
        result = normalize_snapshots([snapshot], as_of=AS_OF, require_volume=True)

        self.assertEqual(result.quality, "PARTIAL")
        self.assertEqual(result.bars[0].volume, None)
        self.assertFalse(result.allows_confirmed)
        event_types = {event.event_type for event in result.quality_events}
        self.assertIn("partial_snapshot", event_types)
        self.assertIn("volume_missing", event_types)


if __name__ == "__main__":
    unittest.main()
