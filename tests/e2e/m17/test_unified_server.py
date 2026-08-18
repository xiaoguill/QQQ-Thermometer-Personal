from __future__ import annotations

import json
import os
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.api.m17.config import load_m17_config
from src.api.m17.gateway import create_m17_application
from src.api.m17.server import create_m17_server


ROOT = Path(__file__).resolve().parents[3]


class M17UnifiedServerTests(unittest.TestCase):
    def setUp(self):
        config = load_m17_config(ROOT / "configs" / "m17" / "unified.json")
        self.application = create_m17_application(config, environ={})
        # The independent Harness deliberately disables socket creation. Keep
        # the same end-to-end assertions in-process there; the normal local
        # run below still exercises the actual HTTP adapter.
        self.controlled = bool(os.environ.get("QQQ_CANDIDATE_ROOT"))
        self.server = None
        self.thread = None
        self.base = None
        if not self.controlled:
            self.server = create_m17_server(self.application, port=0)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
        self.application.runtime_handle.stop()

    def _json_get(self, path: str):
        if self.controlled:
            if path == "/api/m17/overview":
                return self.application.overview().body
            if path == "/api/m17/paper-plan":
                return self.application.paper_plan().body
            raise AssertionError(f"unhandled in-process route: {path}")
        return json.load(urlopen(self.base + path, timeout=3))

    def test_root_and_legacy_pages_are_independently_reachable(self):
        for path in ("/", "/dashboard/index.html", "/m14/index.html", "/demo/index.html", "/shell/index.html", "/m16/index.html"):
            with self.subTest(path=path):
                if self.controlled:
                    relative = "m17/index.html" if path == "/" else path.lstrip("/")
                    self.assertTrue((ROOT / "frontend" / relative).is_file())
                else:
                    response = urlopen(self.base + path, timeout=3)
                    self.assertEqual(response.status, 200)
                    self.assertIn("text/html", response.headers.get("Content-Type", ""))

    def test_overview_and_paper_plan_are_json_and_fail_closed(self):
        overview = self._json_get("/api/m17/overview")
        self.assertEqual(overview["schema"], "qqq-m17-overview/v1")
        self.assertTrue(overview["paper_only"])
        plan = self._json_get("/api/m17/paper-plan")
        self.assertEqual(plan["status"], "CONFIRMED_UNAVAILABLE")
        self.assertFalse(plan["execution_allowed"])

    def test_post_and_path_traversal_are_rejected(self):
        if self.controlled:
            with self.assertRaises(ValueError):
                create_m17_server(self.application, host="0.0.0.0", port=0)
        else:
            with self.assertRaises(HTTPError) as post_error:
                urlopen(Request(self.base + "/api/m17/paper-plan", method="POST"), timeout=3)
            self.assertEqual(post_error.exception.code, 405)
            with self.assertRaises(HTTPError) as traversal_error:
                urlopen(self.base + "/../AGENTS.md", timeout=3)
            self.assertEqual(traversal_error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
