from __future__ import annotations

import http.client
from pathlib import Path
import tempfile
import threading
import unittest
import json

from src.api.live_api import LiveApiService
from src.api.live_server import create_live_server
from src.api.read_api import ApiAccessPolicy
from src.notifications import LiveEventBus
from src.storage import SQLiteRepository, SQLiteStore
from tests.realtime.test_massive_client import NOW


ROOT = Path(__file__).resolve().parents[2]


class LiveServerTests(unittest.TestCase):
    def _start(self):
        bus = LiveEventBus()
        service = LiveApiService(bus, access_policy=ApiAccessPolicy(access_token="test-token"), heartbeat_seconds=0.05)
        server = create_live_server(service, port=0, static_root=ROOT / "frontend" / "m16")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, bus

    def test_static_page_is_served_only_from_local_root(self):
        server, thread, _ = self._start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertIn("Northstar Live", body)

            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/..%2FREADME.md")
            traversal = connection.getresponse()
            traversal.read()
            connection.close()
            self.assertEqual(traversal.status, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_sse_http_response_is_private_and_replayable(self):
        server, thread, bus = self._start()
        bus.publish_service_status("ready", occurred_at_utc=NOW)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.request("GET", "/api/live/events", headers={"X-QQQ-Local-Token": "test-token"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
            self.assertEqual(response.readline(), b": connected\n")
            self.assertEqual(response.readline(), b"\n")
            self.assertTrue(response.readline().startswith(b"id: evt-"))
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_sse_http_last_event_id_replays_only_events_after_cursor(self):
        server, thread, bus = self._start()
        first = bus.publish_service_status("ready", occurred_at_utc=NOW)
        second = bus.publish_service_status("degraded", occurred_at_utc=NOW)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/api/live/events", headers={"X-QQQ-Local-Token": "test-token"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b": connected\n")
            self.assertEqual(response.readline(), b"\n")
            first_frame = b"".join(response.readline() for _ in range(4))
            self.assertIn(first.event_id.encode("utf-8"), first_frame)
            self.assertNotIn(second.event_id.encode("utf-8"), first_frame)
            connection.close()

            reconnect = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            reconnect.request(
                "GET",
                "/api/live/events",
                headers={"X-QQQ-Local-Token": "test-token", "Last-Event-ID": first.event_id},
            )
            replay = reconnect.getresponse()
            self.assertEqual(replay.status, 200)
            self.assertEqual(replay.readline(), b": connected\n")
            self.assertEqual(replay.readline(), b"\n")
            second_frame = b"".join(replay.readline() for _ in range(4))
            self.assertIn(second.event_id.encode("utf-8"), second_frame)
            self.assertNotIn(first.event_id.encode("utf-8"), second_frame)
            reconnect.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_confirmed_endpoint_is_explicitly_unavailable_without_read_model(self):
        server, thread, _ = self._start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/api/thermometer/latest", headers={"X-QQQ-Local-Token": "test-token"})
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 503)
            self.assertEqual(body["error"]["code"], "CONFIRMED_UNAVAILABLE")
            self.assertEqual(body["meta"]["data_quality"], "failed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_confirmed_endpoint_rejects_query_parameters(self):
        server, thread, _ = self._start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            connection.request("GET", "/api/thermometer/latest?unexpected=1", headers={"X-QQQ-Local-Token": "test-token"})
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 400)
            self.assertEqual(body["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(body["meta"]["data_quality"], "failed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_confirmed_endpoint_mounts_existing_read_model_without_writes(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            database = root / "confirmed.sqlite"
            store = SQLiteStore(database, allowed_root=root).initialize()
            try:
                repository = SQLiteRepository(store)
                repository.put_regime_snapshot(
                    "regime|2024-01-03",
                    {"state": "normal", "reason_codes": ["medium_gate_confirmed"]},
                    signal_date="2024-01-03",
                    execution_date="2024-01-04",
                    as_of="2024-01-03T22:00:00Z",
                    strategy_version="v10_preserve_shock_recovery",
                    state="normal",
                    quality="OK",
                )
                policy = ApiAccessPolicy(access_token="test-token")
                bus = LiveEventBus()
                service = LiveApiService(
                    bus,
                    access_policy=policy,
                    confirmed_read_model_path=database,
                )
                server = create_live_server(service, port=0, static_root=ROOT / "frontend" / "m16")
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
                    connection.request("GET", "/api/thermometer/latest", headers={"X-QQQ-Local-Token": "test-token"})
                    response = connection.getresponse()
                    body = json.loads(response.read().decode("utf-8"))
                    connection.close()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(body["data"]["state"], "normal")
                    self.assertEqual(body["meta"]["strategy_version"], "v10_preserve_shock_recovery")
                    self.assertEqual(repository.count("regime_snapshot"), 1)
                    self.assertEqual(bus.events_after().events[0].event_type, "state.candidate")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
            finally:
                store.close()
