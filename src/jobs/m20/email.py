"""Plain-language, HTTPS-only email publisher for M20.

The publisher receives a local M19 ``decision.json`` and never receives a
Massive key.  Secrets are read only from the process environment at send time;
the rendered body and subject contain no credentials.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


class EmailError(RuntimeError):
    """Raised when the email boundary is not configured or cannot send."""


_STATE_LABELS = {
    "normal": "正常",
    "shock": "冲击",
    "recovery": "恢复",
    "warming": "预热",
    "needs_review": "需要复核",
    None: "未知",
}
_TREND_LABELS = {
    "bullish": "偏强",
    "bearish": "偏弱",
    "mixed": "多空混合",
    "neutral": "中性",
    "unavailable": "不可用",
    None: "未知",
}
_WEIGHT_ORDER = ("QQQ", "QLD", "VXX", "BIL", "TLT", "IAU", "XLU", "SVXY")


def _read_decision(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmailError("decision.json cannot be read") from exc
    if not isinstance(value, Mapping) or value.get("schema") != "qqq-m19-decision/v1":
        raise EmailError("decision.json has an unsupported schema")
    return value


def _percent(value: Any, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    number = float(value) * 100.0
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def _weight_lines(weights: Mapping[str, Any]) -> list[str]:
    if not weights:
        return ["- 暂无（数据不完整，不能生成目标仓位）"]
    names = list(_WEIGHT_ORDER) + sorted(set(weights) - set(_WEIGHT_ORDER))
    return [f"- {name}: {float(weights[name]) * 100:.1f}%" for name in names if name in weights and float(weights[name]) > 1e-12]


def _simple_reason(decision: Mapping[str, Any]) -> str:
    reasons = decision.get("reason_codes")
    if not isinstance(reasons, list) or not reasons:
        return "模型没有返回额外说明。"
    translations = {
        "shock_candidate_profile": "模型识别到冲击状态，进入防守候选仓位。",
        "recovery_candidate_profile": "模型识别到恢复状态，按候选仓位逐步恢复。",
        "normal_medium_gate_confirmed": "中期趋势已连续满足确认条件。",
        "medium_gate_confirmation_pending": "中期趋势还在确认中。",
        "warmup_default_weights": "数据处于预热阶段，先使用现金代理。",
        "data_quality_fail_closed": "数据质量没有达到发布条件。",
        "fresh_shock_not_confirmed": "目前没有出现需要进入冲击防守的条件。",
        "explanation_close_confirmed": "已使用完整收盘数据确认。",
        "candidate_strategy_not_product_default": "当前仍是研究候选策略，只提供纸上目标。",
    }
    translated = [translations.get(str(item), "模型已完成对应条件检查。") for item in reasons[:4]]
    return "；".join(dict.fromkeys(translated))


def build_email(decision: Mapping[str, Any], *, test_email: bool = False) -> tuple[str, str]:
    status = str(decision.get("status", "DATA_ERROR"))
    state_label = _STATE_LABELS.get(decision.get("state"), "未知")
    changed = decision.get("target_changed_from_previous_signal")
    if status in {"DATA_ERROR", "NEEDS_REVIEW"}:
        subject = "[QQQ策略] 数据异常，暂不调仓"
        conclusion = "今天没有得到达到发布条件的目标仓位，暂不调仓。"
    elif changed is True:
        subject = f"[QQQ策略] 下一交易日目标有变化｜状态：{state_label}"
        conclusion = "下一交易日的纸上目标有变化，请人工核对后再决定是否调整。"
    else:
        subject = f"[QQQ策略] 今日无需变更目标｜状态：{state_label}"
        conclusion = "目前没有发现需要改变纸上目标的信号。"
    if test_email:
        subject = "[测试] " + subject

    signal_date = decision.get("signal_date") or "—"
    execution_date = decision.get("execution_date") or "—"
    temperature = decision.get("temperature")
    temperature_text = "—" if temperature is None else f"{float(temperature):.0f}/100"
    lines = [
        "QQQ 温度计·每日简报",
        "",
        f"结论：{conclusion}",
        "",
        f"信号日期：{signal_date}",
        f"计划执行日：{execution_date}",
        f"当前状态：{state_label}",
        f"温度：{temperature_text}",
        f"趋势：{_TREND_LABELS.get(decision.get('trend'), '未知')}",
        "",
        "目标仓位：",
        *_weight_lines(decision.get("target_weights") if isinstance(decision.get("target_weights"), Mapping) else {}),
        "",
        f"为什么：{_simple_reason(decision)}",
        f"数据情况：{decision.get('data_quality') or 'FAILED'}",
        f"数据来源：{(decision.get('source') or {}).get('provider', '—') if isinstance(decision.get('source'), Mapping) else '—'}",
        f"模型版本：{decision.get('strategy_version', '—')}（回放标签 {decision.get('replay_version', '—')}）",
        "",
        "你需要知道：",
        "1. 这是收盘后生成的纸上建议，不是自动下单指令。",
        "2. 程序不会读取券商账户，也不会替你买卖。",
        "3. 目标只使用信号日收盘数据，实际执行日是下一个美股交易日。",
        "4. 如果数据异常，程序会停止发布目标仓位，不会拿 VIX、SVXY 或 BIL 冒充 VXX。",
    ]
    if status in {"DATA_ERROR", "NEEDS_REVIEW"}:
        lines[2] = f"结论：{conclusion}"
        lines.extend(["", f"错误代码：{decision.get('failure_code', 'NEEDS_REVIEW')}", f"说明：{decision.get('failure_message', '指标或数据质量尚未达到发布条件，需要人工检查。')}"])
    return subject, "\n".join(lines) + "\n"


def _email_window_ok(*, now: datetime, start: str, end: str) -> bool:
    start_time = time.fromisoformat(start)
    end_time = time.fromisoformat(end)
    return start_time <= now.timetz().replace(tzinfo=None) < end_time


def _recipients(raw: str) -> list[str]:
    values = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    if not values or any("\n" in item or "\r" in item for item in values):
        raise EmailError("QQQ_EMAIL_TO is missing or invalid")
    return values


def _post_json(url: str, headers: Mapping[str, str], payload: Mapping[str, Any]) -> None:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", **dict(headers)},
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URL is an allowlisted HTTPS endpoint below
            if not 200 <= int(response.status) < 300:
                raise EmailError("email provider rejected the request")
            response.read(512)
    except HTTPError as exc:
        raise EmailError(f"email provider returned HTTP {exc.code}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise EmailError("email provider request failed") from exc


def send_email(subject: str, body: str, *, environ: Mapping[str, str] | None = None) -> None:
    values = os.environ if environ is None else environ
    provider = str(values.get("EMAIL_API_PROVIDER", "")).strip().lower()
    api_key = values.get("EMAIL_API_KEY")
    sender = values.get("EMAIL_API_FROM")
    recipients = _recipients(values.get("QQQ_EMAIL_TO", ""))
    if provider not in {"resend", "brevo", "sendgrid"}:
        raise EmailError("EMAIL_API_PROVIDER must be resend, brevo, or sendgrid")
    if not api_key or not sender:
        raise EmailError("EMAIL_API_KEY and EMAIL_API_FROM are required")
    if "\n" in sender or "\r" in sender:
        raise EmailError("EMAIL_API_FROM is invalid")
    if provider == "resend":
        _post_json(
            "https://api.resend.com/emails",
            {"Authorization": f"Bearer {api_key}"},
            {"from": sender, "to": recipients, "subject": subject, "text": body},
        )
    elif provider == "brevo":
        _post_json(
            "https://api.brevo.com/v3/smtp/email",
            {"api-key": api_key},
            {"sender": {"email": sender}, "to": [{"email": item} for item in recipients], "subject": subject, "textContent": body},
        )
    else:
        _post_json(
            "https://api.sendgrid.com/v3/mail/send",
            {"Authorization": f"Bearer {api_key}"},
            {"personalizations": [{"to": [{"email": item} for item in recipients]}], "from": {"email": sender}, "subject": subject, "content": [{"type": "text/plain", "value": body}]},
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one M20 plain-language QQQ email.")
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--preview", type=Path, help="Write the subject and body without sending.")
    parser.add_argument("--dry-run", action="store_true", help="Only render the email; never contact a provider.")
    parser.add_argument("--test-email", action="store_true", help="Mark as a test and allow a manual run outside the morning window.")
    parser.add_argument("--email-window-start", default="08:00")
    parser.add_argument("--email-window-end", default="10:00")
    args = parser.parse_args(argv)
    try:
        decision = _read_decision(args.decision)
        subject, body = build_email(decision, test_email=args.test_email)
        preview = args.preview or args.decision.with_name("email_preview.txt")
        preview.write_text(f"Subject: {subject}\n\n{body}", encoding="utf-8")
        if not args.test_email and not args.dry_run:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            if not _email_window_ok(now=now, start=args.email_window_start, end=args.email_window_end):
                raise EmailError("normal email is only allowed between 08:00 and 10:00 Asia/Shanghai")
        if not args.dry_run:
            send_email(subject, body)
    except EmailError as exc:
        print(f"M20 email failed: {exc}")
        return 2
    action = "preview rendered" if args.dry_run else "email sent"
    print(f"M20 {action}; preview={args.preview or args.decision.with_name('email_preview.txt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EmailError", "build_email", "send_email"]
