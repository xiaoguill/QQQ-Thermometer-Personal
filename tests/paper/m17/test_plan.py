from __future__ import annotations

import unittest

from src.paper.m17.plan import PaperInput, PaperPlanError, build_paper_plan, empty_paper_plan


def _paper(*, cash: float = 1_000, positions: dict | None = None) -> PaperInput:
    return PaperInput.from_mapping(
        {
            "$schema": "qqq-m17-paper-input/v1",
            "portfolio_id": "test-paper",
            "base_currency": "USD",
            "starting_cash": cash,
            "positions": positions or {},
        }
    )


class PaperPlanTests(unittest.TestCase):
    def test_empty_input_is_explicitly_required(self):
        plan = build_paper_plan(
            target_weights={"QQQ": 1.0},
            paper_input=_paper(cash=0),
            prices={"QQQ": {"price": 100, "quality": "OK", "provisional": True}},
            as_of="2026-08-18T09:30:00+08:00",
        )
        self.assertEqual(plan["status"], "INPUT_REQUIRED")
        self.assertTrue(plan["paper_only"])
        self.assertFalse(plan["execution_allowed"])
        self.assertFalse(plan["order_created"])

    def test_ready_plan_uses_confirmed_targets_and_explicit_prices(self):
        plan = build_paper_plan(
            target_weights={"QQQ": 0.5, "BIL": 0.5},
            paper_input=_paper(cash=1_000, positions={"QQQ": {"quantity": 1, "average_cost": 90}}),
            prices={
                "QQQ": {"price": 100, "quality": "OK", "provisional": True},
                "BIL": {"price": 100, "quality": "OK", "provisional": True},
            },
            as_of="2026-08-18T09:30:00+08:00",
            strategy_meta={"confirmed": True, "provisional": False},
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["portfolio"]["estimated_nav"], 1_100.0)
        self.assertEqual([item["symbol"] for item in plan["actions"]], ["BIL", "QQQ"])
        self.assertTrue(all(item["not_order"] for item in plan["actions"]))
        self.assertFalse(plan["order_created"])

    def test_bad_quality_fails_closed(self):
        plan = build_paper_plan(
            target_weights={"QQQ": 1.0},
            paper_input=_paper(),
            prices={"QQQ": {"price": 100, "quality": "STALE", "provisional": True}},
            as_of="2026-08-18T09:30:00+08:00",
        )
        self.assertEqual(plan["status"], "DATA_QUALITY_FAILED")
        self.assertFalse(plan["execution_allowed"])
        self.assertEqual(plan["quality_issues"][0]["quality"], "STALE")

    def test_target_weights_must_sum_to_one(self):
        plan = build_paper_plan(
            target_weights={"QQQ": 0.8},
            paper_input=_paper(),
            prices={"QQQ": {"price": 100, "quality": "OK", "provisional": True}},
            as_of="2026-08-18T09:30:00+08:00",
        )
        self.assertEqual(plan["status"], "TARGET_INVALID")

    def test_negative_holding_is_rejected(self):
        with self.assertRaises(PaperPlanError):
            _paper(positions={"QQQ": {"quantity": -1}})

    def test_empty_envelope_always_declares_no_execution(self):
        plan = empty_paper_plan("CONFIRMED_UNAVAILABLE", reason="offline")
        self.assertTrue(plan["paper_only"])
        self.assertFalse(plan["execution_allowed"])
        self.assertFalse(plan["order_created"])


if __name__ == "__main__":
    unittest.main()
