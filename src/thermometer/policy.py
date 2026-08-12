"""Small deterministic v10 policy boundary used by the verification path.

This is intentionally a pure function.  It is not a complete production
strategy engine; it is the first executable contract surface that Golden
cases can independently challenge without passing expected state or weights
into the candidate as input.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import load_contract


STRATEGY_VERSION = "v10_preserve_shock_recovery"
POLICY_IMPLEMENTATION_VERSION = "candidate-policy-contract-v1"


def _weights(**values: float) -> dict[str, float]:
    return {symbol: float(value) for symbol, value in values.items() if value != 0.0}


def generate_target_snapshot(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Derive state and target weights from signal-date-only feature inputs."""

    required = {"strategy_version", "signal_date", "execution_date", "indicators", "input_data_version"}
    missing = sorted(required - set(inputs))
    if missing:
        raise ValueError(f"policy inputs missing fields: {missing}")
    if inputs["strategy_version"] != STRATEGY_VERSION:
        raise ValueError(f"unsupported policy strategy version: {inputs['strategy_version']}")
    indicators = inputs["indicators"]
    if not isinstance(indicators, Mapping):
        raise ValueError("policy indicators must be an object")

    quality = indicators.get("quality", "ok")
    ready = indicators.get("ready") is True
    if quality != "ok":
        state = "needs_review"
        weights = _weights(BIL=1.0)
        reason_codes = ["data_quality_needs_review"]
    elif not ready:
        state = "warming"
        weights = _weights(BIL=1.0)
        reason_codes = ["warmup_insufficient_history"]
    else:
        qqq_return_5d = float(indicators.get("qqq_return_5d", 0.0))
        vix = float(indicators.get("vix", 0.0))
        vix_term_ratio = float(indicators.get("vix_term_ratio", 0.0))
        if qqq_return_5d <= -0.05 and (vix >= 30.0 or vix_term_ratio >= 1.0):
            state = "shock"
            weights = _weights(VXX=0.25, BIL=0.75)
            reason_codes = ["shock_entry_price_and_volatility"]
        elif sum(
            bool(indicators.get(name, False))
            for name in ("qqq_rebound", "qqq_above_ema10", "rv20_declining")
        ) >= 2:
            state = "recovery"
            weights = _weights(QQQ=0.5, BIL=0.5)
            reason_codes = ["two_recovery_confirmations"]
        elif indicators.get("qqq_above_sma150") is True and indicators.get("momentum126_positive") is True:
            state = "normal"
            weights = _weights(QQQ=0.6, BIL=0.4)
            reason_codes = ["medium_gate_confirmed"]
        else:
            state = "normal"
            weights = _weights(BIL=1.0)
            reason_codes = ["risk_not_confirmed"]

    snapshot = {
        "strategy_version": inputs["strategy_version"],
        "signal_date": inputs["signal_date"],
        "execution_date": inputs["execution_date"],
        "state": state,
        "target_weights": weights,
        "indicators": dict(indicators),
        "reason_codes": reason_codes,
        "input_data_version": inputs["input_data_version"],
    }
    # Validate the generated object at the candidate boundary, but do not use
    # validation to manufacture its state or target weights.
    load_contract().validate_target_snapshot(snapshot)
    return snapshot
