from __future__ import annotations

import unittest

from src.jobs.m18.massive_history import MassiveDailyHistoryAdapter
from src.realtime.config import RealtimeConfig
from src.realtime.massive_client import MassiveClient, TransportResponse


class _FakeTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, str]]] = []

    def request(self, url: str, *, headers: dict[str, str], timeout: int) -> TransportResponse:
        self.calls.append((url, dict(headers)))
        if self.status_code != 200:
            return TransportResponse(self.status_code, {"status": "ERROR"}, {})
        return TransportResponse(
            200,
            {
                "status": "OK",
                "request_id": "request-1",
                "next_url": "https://api.massive.com/next?apiKey=should-not-persist",
                "token": "should-not-persist",
                "results": [
                    {"t": 1_704_204_800_000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000},
                    {"t": 1_704_291_200_000, "o": 101, "h": 103, "l": 100, "c": 102, "v": 1100},
                ],
            },
            {},
        )


def _config() -> RealtimeConfig:
    return RealtimeConfig.from_mapping(
        {
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
        }
    )


class MassiveHistoryAdapterTests(unittest.TestCase):
    def test_fetch_maps_index_tickers_to_m02_symbols_and_never_persists_key(self) -> None:
        transport = _FakeTransport()
        client = MassiveClient(_config(), "test-secret", transport=transport)
        snapshots = MassiveDailyHistoryAdapter(_config(), client).fetch(
            start_date="2024-01-01",
            end_date="2024-01-10",
            retrieved_at="2024-01-11T00:00:00Z",
        )

        self.assertEqual(len(snapshots), 3)
        requests = [dict(snapshot.request) for snapshot in snapshots]
        self.assertEqual(
            {tuple(request["symbols"])[0] for request in requests},
            {"QQQ", "VIX", "VIX3M"},
        )
        self.assertTrue(all(snapshot.quality == "OK" for snapshot in snapshots))
        self.assertEqual(snapshots[0].payload["bars"][0]["symbol"], "QQQ")
        serialized = str([snapshot.as_record() for snapshot in snapshots])
        self.assertNotIn("test-secret", serialized)
        self.assertNotIn("should-not-persist", serialized)
        self.assertTrue(all("Authorization" not in url for url, _ in transport.calls))
        self.assertTrue(all(headers["Authorization"] == "Bearer test-secret" for _, headers in transport.calls))

    def test_provider_entitlement_failure_is_fail_closed(self) -> None:
        transport = _FakeTransport(status_code=403)
        client = MassiveClient(_config(), "test-secret", transport=transport)
        snapshots = MassiveDailyHistoryAdapter(_config(), client).fetch(
            start_date="2024-01-01",
            end_date="2024-01-10",
            symbols=("QQQ",),
            retrieved_at="2024-01-11T00:00:00Z",
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].status, "failed")
        self.assertEqual(snapshots[0].quality, "FAILED")
        self.assertEqual(snapshots[0].error_code, "NOT_ENTITLED")


if __name__ == "__main__":
    unittest.main()
