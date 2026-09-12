from __future__ import annotations

from src.jobs.m21.email import build_email


def test_incomplete_data_email_is_plain_and_stops_rebalancing() -> None:
    subject, body = build_email(
        {
            "schema": "qqq-m21-decision/v1",
            "status": "DATA_ERROR",
            "failure_code": "NOT_ENTITLED",
            "failure_message": "Massive denied access to VXX.",
            "failure_class": "permission",
            "manual_action": "数据不完整，本次不调仓。",
            "signal_date": None,
            "execution_date": None,
            "state": None,
            "trend": None,
            "temperature": None,
            "target_weights": {},
            "data_quality": "FAILED",
            "source": {"provider": "free_close"},
            "strategy_version": "v10_preserve_shock_recovery",
            "replay_version": "v12.2-causal-walk-forward/v1",
            "availability": {
                "vxx_diagnostic": {
                    "declared_in_stock_contract": True,
                    "outcome": "failed",
                    "failure_class": "permission",
                }
            },
        }
    )

    assert subject == "[QQQ策略] 数据不完整，本次不调仓"
    assert "结论：数据不完整，本次不调仓。" in body
    assert "VXX" in body
    assert "分类：permission" in body
    assert "Massive denied access to VXX." in body
    assert "MASSIVE_API_KEY" not in body


def test_ready_email_shows_target_without_claiming_auto_execution() -> None:
    subject, body = build_email(
        {
            "schema": "qqq-m21-decision/v1",
            "status": "READY",
            "signal_date": "2026-09-11",
            "execution_date": "2026-09-14",
            "state": "recovery",
            "trend": "bullish",
            "temperature": 64.0,
            "target_weights": {"QQQ": 0.60, "BIL": 0.40},
            "target_changed_from_previous_signal": True,
            "data_quality": "PASS",
            "source": {"provider": "free_close"},
            "strategy_version": "v10_preserve_shock_recovery",
            "replay_version": "v12.2-causal-walk-forward/v1",
            "reason_codes": ["recovery_candidate_profile"],
            "availability": {"vxx_diagnostic": {"outcome": "available"}},
        }
    )

    assert subject == "[QQQ策略] 下一交易日目标有变化｜状态：恢复"
    assert "- QQQ：60.0%" in body
    assert "- BIL：40.0%" in body
    assert "不是自动下单指令" in body
    assert "实际执行日是下一个美股交易日" in body
