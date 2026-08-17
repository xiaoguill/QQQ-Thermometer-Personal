from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
M14 = ROOT / "frontend" / "m14"


class M14ContractTests(unittest.TestCase):
    def test_m14_isolated_files_exist(self) -> None:
        for name in (
            "index.html",
            "styles.css",
            "m14.mjs",
            "view-model.mjs",
            "view-model.test.mjs",
            "fixtures.mjs",
            "README.md",
        ):
            self.assertTrue((M14 / name).is_file(), name)

    def test_page_exposes_replay_performance_and_audit_surfaces(self) -> None:
        html = (M14 / "index.html").read_text(encoding="utf-8")
        for marker in (
            'data-history-body',
            'data-curve-body',
            'data-metric-list',
            'data-annual-returns',
            'data-annual-drawdowns',
            'data-benchmark-list',
            'data-cost-list',
            'data-ledger-body',
            'data-versions-body',
            'data-provenance-body',
            'data-audit-fields',
            'data-quality-issues',
            'data-missing-notice',
            'data-control-from',
            'data-control-to',
            'data-control-as-of',
        ):
            self.assertIn(marker, html)
        for text in ("SIMULATED", "未提供", "未验证", "不计算", "只读"):
            self.assertIn(text, html)

    def test_runtime_uses_only_m14_read_endpoints(self) -> None:
        source = (M14 / "m14.mjs").read_text(encoding="utf-8")
        for method in (
            "getHistory",
            "getPerformanceCurve",
            "getPerformanceMetrics",
            "getLedger",
            "getVersions",
            "getDataQuality",
        ):
            self.assertIn(f"client.{method}", source)
        for forbidden in (
            "getLatest",
            "getPortfolioLatest",
            "getPortfolioTargets",
            "getNextTriggers",
            "confirmPaper",
        ):
            self.assertNotIn(f"client.{forbidden}", source)
        self.assertIn("from: controls.from", source)
        self.assertIn("to: controls.to", source)
        self.assertIn("as_of: controls.asOf", source)
        self.assertIn("limit: controls.limit", source)

    def test_runtime_is_presentation_only_and_fail_closed(self) -> None:
        runtime = "\n".join(
            (M14 / name).read_text(encoding="utf-8")
            for name in ("m14.mjs", "view-model.mjs", "fixtures.mjs", "index.html")
        )
        for forbidden in (
            "innerHTML",
            "localStorage",
            "sessionStorage",
            "document.cookie",
            "XMLHttpRequest",
            "fetch(",
            "method: \"POST\"",
            "method: \"PUT\"",
            "method: \"DELETE\"",
            "broker",
            "order",
            "transfer",
            "secret",
            "Math.",
        ):
            self.assertNotIn(forbidden, runtime)
        self.assertIn("textContent", runtime)
        self.assertIn('kind === "error"', runtime)
        self.assertIn("未提供", runtime)
        self.assertIn("不自行计算", runtime)

    def test_backend_values_are_not_reconstructed_in_browser(self) -> None:
        model = (M14 / "view-model.mjs").read_text(encoding="utf-8")
        runtime = (M14 / "m14.mjs").read_text(encoding="utf-8")
        for field in (
            '"annual_returns"',
            '"annual_drawdowns"',
            '"qqq_nav"',
            '"qqq_return"',
            '"drawdown"',
            '"cost_bps"',
            '"cost_adjusted_nav"',
            '"data_manifest"',
        ):
            self.assertIn(field, model + runtime)
        self.assertNotIn("reduce(", model + runtime)
        self.assertNotIn("annualize", model + runtime)
        self.assertNotIn("calculate", model + runtime)
        self.assertIn("后端未提供逐年收益", runtime)
        self.assertIn("后端未提供逐年回撤", runtime)
        self.assertIn("后端未提供 QQQ 基准", runtime)
        self.assertIn("后端未提供成本压力场景", runtime)

    def test_responsive_accessible_and_no_external_source(self) -> None:
        css = (M14 / "styles.css").read_text(encoding="utf-8")
        html = (M14 / "index.html").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertRegex(css, r"@media\s*\(max-width:")
        self.assertIn('aria-live="polite"', html)
        self.assertNotRegex(html + css, r"https?://")


if __name__ == "__main__":
    unittest.main()
