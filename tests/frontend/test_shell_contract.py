from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "frontend" / "shell"


class FrontendShellContractTests(unittest.TestCase):
    def test_expected_shell_files_exist(self) -> None:
        for name in (
            "index.html",
            "tokens.css",
            "styles.css",
            "fixtures.mjs",
            "api-client.mjs",
            "shell.mjs",
            "README.md",
            "api-client.test.mjs",
        ):
            self.assertTrue((SHELL / name).is_file(), name)

    def test_shell_exposes_all_data_quality_states(self) -> None:
        html = (SHELL / "index.html").read_text(encoding="utf-8")
        for state in ("loading", "empty", "stale", "partial", "failed", "needs_review"):
            self.assertIn(f'data-fixture="{state}"', html)
            self.assertIn(f'data-fixture-card="{state}"', html)
        self.assertIn('data-mode="SIMULATED"', html)
        self.assertIn('data-metadata="time"', html)
        self.assertIn('data-metadata="confirmation"', html)
        self.assertIn('data-metadata="version"', html)
        self.assertIn('data-metadata="quality"', html)

    def test_api_client_is_same_origin_read_only(self) -> None:
        source = (SHELL / "api-client.mjs").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"https?://|\\\\")
        self.assertNotIn("paper/confirm", source)
        self.assertNotIn('method: "POST"', source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("document.cookie", source)
        self.assertIn('method: "GET"', source)
        for endpoint in (
            "/thermometer/latest",
            "/thermometer/history",
            "/signals/explain",
            "/triggers/next",
            "/portfolio/targets",
            "/portfolio/latest",
            "/portfolio/ledger",
            "/performance/curve",
            "/performance/metrics",
            "/data-quality/latest",
            "/versions",
        ):
            self.assertIn(endpoint, source)

    def test_shell_does_not_render_untrusted_html(self) -> None:
        source = (SHELL / "shell.mjs").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", source)
        self.assertIn("textContent", source)

    def test_responsive_and_accessible_tokens_exist(self) -> None:
        css = (SHELL / "styles.css").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertRegex(css, r"@media\s*\(max-width:")
        self.assertIn("--ns-stale", (SHELL / "tokens.css").read_text(encoding="utf-8"))
        self.assertIn("--ns-review", (SHELL / "tokens.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
