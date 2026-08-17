from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend" / "dashboard"


class DashboardContractTests(unittest.TestCase):
    def test_expected_files_exist(self) -> None:
        for name in (
            "index.html",
            "styles.css",
            "fixtures.mjs",
            "view-model.mjs",
            "view-model.test.mjs",
            "dashboard.mjs",
            "README.md",
        ):
            self.assertTrue((DASHBOARD / name).is_file(), name)

    def test_dashboard_displays_required_provenance_and_boundary(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        for marker in (
            'data-metadata-grid',
            'data-confirmation-value',
            'data-confirmation-note',
            'data-quality-value',
            'data-weight-list',
            'data-reason-list',
            'data-indicator-list',
            'data-missing-fields',
            'data-mode-label',
        ):
            self.assertIn(marker, html)
        self.assertIn("confirmation_status", html)
        self.assertIn("不会", html)

    def test_runtime_never_adds_write_or_strategy_capability(self) -> None:
        runtime = "\n".join(
            (DASHBOARD / name).read_text(encoding="utf-8")
            for name in ("dashboard.mjs", "view-model.mjs", "index.html")
        )
        self.assertNotIn("innerHTML", runtime)
        self.assertNotIn("localStorage", runtime)
        self.assertNotIn("document.cookie", runtime)
        self.assertNotIn("paper/confirm", runtime)
        self.assertNotRegex(runtime, r"https?://")
        self.assertNotRegex(runtime, r"\b(?:buy|sell|order|broker)\b")

    def test_target_weights_are_iterated_from_payload(self) -> None:
        source = (DASHBOARD / "dashboard.mjs").read_text(encoding="utf-8")
        model = (DASHBOARD / "view-model.mjs").read_text(encoding="utf-8")
        self.assertIn("Object.entries(rawWeights)", model)
        self.assertIn("appendWeightRows", source)
        self.assertNotIn("QQQ", source)
        self.assertNotIn("QLD", source)
        self.assertNotIn("VIX", source)

    def test_dashboard_uses_only_m13_read_endpoints(self) -> None:
        source = (DASHBOARD / "dashboard.mjs").read_text(encoding="utf-8")
        for method in ("getLatest", "explainSignals", "getDataQuality"):
            self.assertRegex(source, rf"client\.{method}\(")
        for forbidden in (
            "getHistory",
            "getLedger",
            "getPerformanceCurve",
            "getPerformanceMetrics",
            "getPortfolioLatest",
            "getNextTriggers",
        ):
            self.assertNotRegex(source, rf"client\.{forbidden}\(")

    def test_confirmation_is_not_inferred_and_failure_clears_old_values(self) -> None:
        model = (DASHBOARD / "view-model.mjs").read_text(encoding="utf-8")
        runtime = (DASHBOARD / "dashboard.mjs").read_text(encoding="utf-8")
        self.assertIn('key: "not_provided"', model)
        self.assertIn("不根据日期或质量推断", runtime)
        self.assertIn("clearRenderedCollections", runtime)
        self.assertIn("UNAVAILABLE_METADATA", runtime)

    def test_repeated_quality_labels_update_together(self) -> None:
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        runtime = (DASHBOARD / "dashboard.mjs").read_text(encoding="utf-8")
        self.assertGreaterEqual(html.count("data-quality-value"), 2)
        self.assertIn("document.querySelectorAll(selector)", runtime)

    def test_responsive_and_state_semantics_exist(self) -> None:
        css = (DASHBOARD / "styles.css").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertRegex(css, r"@media\s*\(max-width:")
        for tone in ("confirmed", "shock", "recovery", "review"):
            self.assertIn(f"state-tone-{tone}", css)


if __name__ == "__main__":
    unittest.main()
