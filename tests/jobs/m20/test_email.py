from __future__ import annotations

from pathlib import Path

import pytest

from src.jobs.m20 import email as email_module
from src.jobs.m20.email import EmailError, build_email, send_email


def _decision() -> dict:
    return {
        "schema": "qqq-m19-decision/v1",
        "status": "READY",
        "state": "normal",
        "temperature": 80,
        "trend": "bullish",
        "signal_date": "2026-08-10",
        "execution_date": "2026-08-11",
        "target_weights": {"QQQ": 0.6, "BIL": 0.4},
        "reason_codes": ["normal_medium_gate_confirmed"],
        "data_quality": "OK",
        "source": {"provider": "local_csv"},
        "strategy_version": "v10_preserve_shock_recovery",
        "replay_version": "v12.2-causal-walk-forward/v1",
        "target_changed_from_previous_signal": True,
    }


def test_email_is_plain_language_and_does_not_show_internal_codes() -> None:
    subject, body = build_email(_decision())
    assert "下一交易日目标有变化" in subject
    assert "中期趋势已连续满足确认条件" in body
    assert "normal_medium_gate_confirmed" not in body
    assert "程序不会读取券商账户" in body


def test_email_failure_contains_no_target() -> None:
    subject, body = build_email({
        "schema": "qqq-m19-decision/v1",
        "status": "DATA_ERROR",
        "failure_code": "NOT_ENTITLED",
        "failure_message": "VXX unavailable",
        "target_weights": {},
        "strategy_version": "v10_preserve_shock_recovery",
        "replay_version": "v12.2-causal-walk-forward/v1",
    })
    assert "数据异常" in subject
    assert "暂不调仓" in body
    assert "VXX unavailable" in body
    assert "目标仓位：\n- 暂无" in body


def test_send_email_uses_allowlisted_provider_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, dict]] = []

    def fake_post(url: str, headers: dict, payload: dict) -> None:
        calls.append((url, headers, payload))

    monkeypatch.setattr(email_module, "_post_json", fake_post)
    send_email(
        "subject",
        "body",
        environ={
            "EMAIL_API_PROVIDER": "resend",
            "EMAIL_API_KEY": "test-secret",
            "EMAIL_API_FROM": "from@example.com",
            "QQQ_EMAIL_TO": "to@example.com",
        },
    )
    assert calls and calls[0][0] == "https://api.resend.com/emails"
    assert calls[0][2]["to"] == ["to@example.com"]


def test_unknown_email_provider_fails_closed() -> None:
    with pytest.raises(EmailError, match="EMAIL_API_PROVIDER"):
        send_email(
            "subject",
            "body",
            environ={
                "EMAIL_API_PROVIDER": "unknown",
                "EMAIL_API_KEY": "test-secret",
                "EMAIL_API_FROM": "from@example.com",
                "QQQ_EMAIL_TO": "to@example.com",
            },
        )
