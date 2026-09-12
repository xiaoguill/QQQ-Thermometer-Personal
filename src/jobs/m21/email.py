"""Plain-language email for the M21 free-close decision.

The actual HTTPS provider implementation is reused from M20.  M21 only
changes the decision schema and adds the explicit free-source/VXX diagnostics;
no credential is accepted as a CLI argument or written to a preview.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.jobs.m20.email import EmailError, send_email

from .config import M21_DECISION_SCHEMA


_STATE_LABELS = {"normal": "正常", "shock": "冲击", "recovery": "恢复", "warming": "预热", "needs_review": "需要复核", None: "未知"}
_TREND_LABELS = {"bullish": "偏强", "bearish": "偏弱", "mixed": "多空混合", "neutral": "中性", None: "未知"}
_WEIGHT_ORDER = ("QQQ", "QLD", "VXX", "BIL", "TLT", "IAU", "XLU", "SVXY")


def _read_decision(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EmailError("decision.json cannot be read") from exc
    if not isinstance(value, Mapping) or value.get("schema") != M21_DECISION_SCHEMA:
        raise EmailError("decision.json has an unsupported M21 schema")
    return value


def _weight_lines(weights: Mapping[str, Any]) -> list[str]:
    if not weights:
        return ["- 暂无（数据不完整，不能生成目标仓位）"]
    names = list(_WEIGHT_ORDER) + sorted(set(weights) - set(_WEIGHT_ORDER))
    return [f"- {name}：{float(weights[name]) * 100:.1f}%" for name in names if name in weights and float(weights[name]) > 1e-12]


def _reason(decision: Mapping[str, Any]) -> str:
    status = str(decision.get("status", "DATA_ERROR"))
    if status != "READY":
        failure_class = decision.get("failure_class")
        if failure_class == "permission":
            return "数据源明确返回无权限，程序没有猜测或替换标的。"
        if failure_class == "interface_or_symbol":
            return "数据接口或标的代码需要检查，程序没有发布新仓位。"
        if failure_class == "symbol_contract":
            return "请求中的标的定义与返回结果不一致，程序没有发布新仓位。"
        return "关键数据没有完整到达，程序没有发布新仓位。"
    reasons = decision.get("reason_codes")
    if not isinstance(reasons, list) or not reasons:
        return "模型完成了收盘条件检查。"
    mapping = {
        "shock_candidate_profile": "市场进入冲击状态，暂时提高防守仓位。",
        "recovery_candidate_profile": "市场进入恢复状态，按候选规则逐步恢复。",
        "normal_medium_gate_confirmed": "中期趋势满足确认条件。",
        "medium_gate_confirmation_pending": "中期趋势仍在确认中。",
        "warmup_default_weights": "数据仍在预热阶段，暂不增加风险暴露。",
        "explanation_close_confirmed": "本次使用了完整收盘数据。",
    }
    return "；".join(dict.fromkeys(mapping.get(str(item), "模型完成了对应条件检查。") for item in reasons[:4]))


def build_email(decision: Mapping[str, Any], *, test_email: bool = False) -> tuple[str, str]:
    status = str(decision.get("status", "DATA_ERROR"))
    state = _STATE_LABELS.get(decision.get("state"), "未知")
    changed = decision.get("target_changed_from_previous_signal")
    if status != "READY":
        subject = "[QQQ策略] 数据不完整，本次不调仓"
        conclusion = "数据不完整，本次不调仓。"
    elif changed is True:
        subject = f"[QQQ策略] 下一交易日目标有变化｜状态：{state}"
        conclusion = "下一交易日的纸上目标有变化，请人工核对后再决定是否调整。"
    else:
        subject = f"[QQQ策略] 今日无需变更目标｜状态：{state}"
        conclusion = "目前没有发现需要改变纸上目标的信号。"
    if test_email:
        subject = "[测试] " + subject
    temperature = decision.get("temperature")
    temperature_text = "—" if temperature is None else f"{float(temperature):.0f}/100"
    source = decision.get("source") if isinstance(decision.get("source"), Mapping) else {}
    availability = decision.get("availability") if isinstance(decision.get("availability"), Mapping) else {}
    vxx = availability.get("vxx_diagnostic") if isinstance(availability.get("vxx_diagnostic"), Mapping) else {}
    lines = [
        "QQQ 温度计·每日收盘简报",
        "",
        f"结论：{conclusion}",
        "",
        f"信号日期：{decision.get('signal_date') or '—'}",
        f"计划执行日：{decision.get('execution_date') or '—'}",
        f"当前状态：{state}",
        f"温度：{temperature_text}",
        f"趋势：{_TREND_LABELS.get(decision.get('trend'), '未知')}",
        "",
        "目标仓位：",
        *_weight_lines(decision.get("target_weights") if isinstance(decision.get("target_weights"), Mapping) else {}),
        "",
        f"为什么：{_reason(decision)}",
        f"数据情况：{decision.get('data_quality') or 'FAILED'}",
        f"数据来源：{source.get('provider', '—')}",
        f"策略版本：{decision.get('strategy_version', '—')}",
        f"回放标签：{decision.get('replay_version', '—')}",
        "",
        "VXX 检查：",
        f"- 配置声明：{'是' if vxx.get('declared_in_stock_contract') else '否'}",
        f"- 本次结果：{vxx.get('outcome', '未完成')}",
        f"- 分类：{vxx.get('failure_class') or '可用'}",
        "",
        "你需要知道：",
        "1. 这是收盘后的纸上建议，不是自动下单指令。",
        "2. 程序不会读取券商账户，也不会替你买卖。",
        "3. 目标只使用已完成的收盘数据，实际执行日是下一个美股交易日。",
        "4. VIX3M 或任何关键标的数据不完整时，不会用其他标的替代。",
    ]
    if status != "READY":
        lines.extend(["", f"错误代码：{decision.get('failure_code', 'UNKNOWN')}", f"说明：{decision.get('failure_message', '需要人工检查数据源。')}"])
    return subject, "\n".join(lines) + "\n"


def _within_window(*, now: datetime, start: str, end: str) -> bool:
    return time.fromisoformat(start) <= now.timetz().replace(tzinfo=None) < time.fromisoformat(end)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one M21 free-close QQQ email.")
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-email", action="store_true")
    parser.add_argument("--email-window-start", default="08:00")
    parser.add_argument("--email-window-end", default="10:00")
    args = parser.parse_args(argv)
    try:
        decision = _read_decision(args.decision)
        subject, body = build_email(decision, test_email=args.test_email)
        preview = args.preview or args.decision.with_name("email_preview.txt")
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(f"Subject: {subject}\n\n{body}", encoding="utf-8")
        if not args.test_email and not args.dry_run:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            if not _within_window(now=now, start=args.email_window_start, end=args.email_window_end):
                raise EmailError("normal email is only allowed between 08:00 and 10:00 Asia/Shanghai")
        if not args.dry_run:
            send_email(subject, body)
    except (EmailError, ValueError) as exc:
        print(f"M21 email failed: {exc}")
        return 2
    action = "preview rendered" if args.dry_run else "email sent"
    print(f"M21 {action}; preview={preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EmailError", "build_email"]
