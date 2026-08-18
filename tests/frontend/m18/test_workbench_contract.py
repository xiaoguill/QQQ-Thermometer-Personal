from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class M18FrontendContractTests(unittest.TestCase):
    def test_workbench_has_all_requested_read_model_regions(self) -> None:
        html = (ROOT / "frontend" / "m18" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "frontend" / "m18" / "app.js").read_text(encoding="utf-8")
        for text in ("最新数据质量", "运行边界", "纸上调仓计划", "确认目标仓位", "确认策略", "实时温度", "M00.5–M17"):
            self.assertIn(text, html)
        self.assertIn("/api/m18/workbench", javascript)
        self.assertNotIn("fetch(\"/api/paper/confirm\"", javascript)
        self.assertNotIn("fetch(\"/api/orders", javascript)

    def test_frontend_does_not_define_strategy_weights_or_thresholds(self) -> None:
        javascript = (ROOT / "frontend" / "m18" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("QQQ: 0.5", javascript)
        self.assertNotIn("QLD: 0.5", javascript)
        self.assertNotIn("threshold", javascript.lower())


if __name__ == "__main__":
    unittest.main()
