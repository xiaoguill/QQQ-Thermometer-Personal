from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from src.realtime import MassiveClient, MissingApiKeyError, RealtimeConfig, TransportResponse
from src.realtime.massive_client import UrllibJsonTransport


NOW = datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc)


def _config():
    return RealtimeConfig.from_mapping({
        "provider": "massive",
        "mode": "rest_poll",
        "base_url": "https://api.massive.com",
        "api_key_env": "MASSIVE_API_KEY",
        "refresh_interval_seconds": 900,
        "request_timeout_seconds": 15,
        "display_timezone": "Asia/Shanghai",
        "market_timezone": "America/New_York",
        "max_source_age_seconds": 1800,
        "future_skew_seconds": 0,
        "symbols": [
            {"symbol": "QQQ", "asset_class": "stocks", "role": "strategy_input"},
            {"symbol": "I:VIX", "asset_class": "indices", "role": "strategy_input"},
            {"symbol": "I:VIX3M", "asset_class": "indices", "role": "strategy_input"},
        ],
    })


class FakeTransport:
    def __init__(self, *, index_status: int = 200, stock_payload: dict | None = None):
        self.index_status = index_status
        self.stock_payload = stock_payload or {
            "request_id": "stock-request-1",
            "ticker": {
                "day": {"c": 500.5, "v": 12345},
                "prevDay": {"c": 499.0},
                "lastTrade": {"p": 500.5, "t": int((NOW - timedelta(minutes=5)).timestamp() * 1_000_000_000)},
                "min": {"c": 500.5, "t": int((NOW - timedelta(minutes=5)).timestamp() * 1000)},
                "updated": int((NOW - timedelta(minutes=5)).timestamp() * 1_000_000_000),
            },
        }
        self.calls: list[tuple[str, dict[str, str]]] = []

    def request(self, url: str, *, headers: dict[str, str], timeout: int) -> TransportResponse:
        self.calls.append((url, headers.copy()))
        parsed = urlsplit(url)
        if parsed.path.startswith("/v2/snapshot"):
            return TransportResponse(200, self.stock_payload, {})
        query = parse_qs(parsed.query)
        ticker = query.get("ticker", [""])[0]
        if self.index_status != 200:
            return TransportResponse(self.index_status, {"status": "ERROR"}, {})
        timestamp = int((NOW - timedelta(minutes=4)).timestamp() * 1_000_000_000)
        value = 14.5 if ticker == "I:VIX" else 22.0
        return TransportResponse(200, {
            "request_id": f"index-{ticker}",
            "results": [{
                "ticker": ticker,
                "value": value,
                "last_updated": timestamp,
                "session": {"close": value, "previous_close": value - 0.5},
            }],
        }, {})


class MassiveClientTests(unittest.TestCase):
    def test_missing_environment_key_is_rejected_without_request(self):
        with self.assertRaises(MissingApiKeyError):
            MassiveClient.from_env(_config(), environ={})

    def test_stock_and_index_snapshots_are_normalized_and_key_stays_in_header(self):
        transport = FakeTransport()
        client = MassiveClient(_config(), "secret-value", transport=transport)
        batch = client.fetch_batch(fetched_at_utc=NOW)
        self.assertEqual(len(batch.observations), 3)
        self.assertTrue(all(item.quality == "OK" for item in batch.observations))
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        vix = next(item for item in batch.observations if item.symbol == "I:VIX")
        self.assertEqual(qqq.last, 500.5)
        self.assertEqual(qqq.price_basis, "unadjusted_ohlcv")
        self.assertEqual(vix.price_basis, "index_level")
        self.assertEqual(vix.last, 14.5)
        self.assertTrue(all("secret-value" not in url for url, _ in transport.calls))
        self.assertTrue(all("apiKey" not in url for url, _ in transport.calls))
        self.assertTrue(all(headers["Authorization"] == "Bearer secret-value" for _, headers in transport.calls))

    def test_not_entitled_is_an_explicit_quality_failure(self):
        batch = MassiveClient(_config(), "secret-value", transport=FakeTransport(index_status=403)).fetch_batch(fetched_at_utc=NOW)
        self.assertEqual(next(item for item in batch.observations if item.symbol == "I:VIX").quality, "NOT_ENTITLED")
        self.assertEqual(next(item for item in batch.observations if item.symbol == "I:VIX").error_code, "NOT_ENTITLED")
        self.assertEqual(next(item for item in batch.observations if item.symbol == "QQQ").quality, "OK")

    def test_missing_source_timestamp_is_partial_not_confirmed(self):
        payload = {"ticker": {"day": {"c": 500.5, "v": 12345}, "prevDay": {"c": 499.0}}}
        batch = MassiveClient(_config(), "secret-value", transport=FakeTransport(stock_payload=payload)).fetch_batch(fetched_at_utc=NOW)
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        self.assertEqual(qqq.quality, "PARTIAL")
        self.assertTrue(qqq.provisional)
        self.assertIsNone(qqq.source_timestamp_utc)

    def test_minute_timestamp_is_used_when_last_trade_is_missing(self):
        timestamp = int((NOW - timedelta(minutes=2)).timestamp() * 1000)
        payload = {"ticker": {"min": {"c": 501.25, "t": timestamp}}}
        batch = MassiveClient(_config(), "secret-value", transport=FakeTransport(stock_payload=payload)).fetch_batch(fetched_at_utc=NOW)
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        self.assertEqual(qqq.quality, "OK")
        self.assertEqual(qqq.last, 501.25)
        self.assertEqual(qqq.source_timestamp_utc, datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc))

    def test_invalid_non_positive_price_is_never_ok(self):
        payload = {"ticker": {"lastTrade": {"p": -1, "t": int(NOW.timestamp() * 1_000_000_000)}}}
        batch = MassiveClient(_config(), "secret-value", transport=FakeTransport(stock_payload=payload)).fetch_batch(fetched_at_utc=NOW)
        qqq = next(item for item in batch.observations if item.symbol == "QQQ")
        self.assertEqual(qqq.quality, "NEEDS_REVIEW")

    def test_http_error_status_is_returned_for_client_classification(self):
        import src.realtime.massive_client as module

        original = module._open_request

        def raise_forbidden(request, *, timeout):
            raise HTTPError(request.full_url, 403, "forbidden", {}, BytesIO(b'{"status":"ERROR"}'))

        module._open_request = raise_forbidden
        try:
            response = UrllibJsonTransport().request(
                "https://api.massive.com/test",
                headers={"Authorization": "Bearer secret-value"},
                timeout=15,
            )
        finally:
            module._open_request = original
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.payload["status"], "ERROR")

    def test_unchanged_provider_content_has_stable_batch_id(self):
        client = MassiveClient(_config(), "secret-value", transport=FakeTransport())
        first = client.fetch_batch(fetched_at_utc=NOW)
        second = client.fetch_batch(fetched_at_utc=NOW + timedelta(seconds=15))
        self.assertEqual(first.batch_id, second.batch_id)
