from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.jobs.m21.config import M21Config
from src.jobs.m21.sources import (
    CboeOfficialSource,
    M21SourceError,
    MassiveFreeStocksSource,
    classify_vxx_issue,
    parse_cboe_history,
)


ROOT = Path(__file__).resolve().parents[3]


def test_cboe_history_parser_normalizes_dates_and_ignores_future_rows() -> None:
    content = (
        b"DATE,OPEN,HIGH,LOW,CLOSE\n"
        b"08/07/2026,20,21,19,20.5\n"
        b"08/10/2026,21,22,20,21.5\n"
        b"08/11/2026,22,23,21,22.5\n"
    )
    values, meta = parse_cboe_history(content, symbol="VIX3M", end_date="2026-08-10")
    assert values == {"2026-08-07": 20.5, "2026-08-10": 21.5}
    assert meta["last_date"] == "2026-08-10"
    assert meta["price_basis"] == "official_index_close"


def test_vix3m_source_unavailable_is_explicit_and_not_substituted() -> None:
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")

    def unavailable(_url: str, _timeout: int) -> bytes:
        raise M21SourceError("NOT_FOUND", "interface_or_symbol", "fixture missing")

    result = CboeOfficialSource(config, fetcher=unavailable).fetch_index("VIX3M", end_date="2026-08-10")
    assert result.status == "failed"
    assert result.failure_code == "NOT_FOUND"
    assert result.failure_class == "interface_or_symbol"
    assert result.rows == ()


@pytest.mark.parametrize(
    ("kwargs", "expected_class", "expected_code"),
    [
        ({"declared_in_config": False, "status": "failed", "error_code": "CONFIG_MISSING_SYMBOL"}, "symbol_contract", "CONFIG_MISSING_SYMBOL"),
        ({"declared_in_config": True, "status": "failed", "error_code": "NOT_ENTITLED"}, "permission", "NOT_ENTITLED"),
        ({"declared_in_config": True, "status": "failed", "error_code": "NOT_FOUND"}, "interface_or_symbol", "NOT_FOUND"),
        ({"declared_in_config": True, "status": "failed", "error_code": "INVALID_PROVIDER_RESPONSE"}, "interface", "INVALID_PROVIDER_RESPONSE"),
        ({"declared_in_config": True, "status": "success"}, None, None),
    ],
)
def test_vxx_failure_layer_is_classified_without_guessing(kwargs: dict[str, object], expected_class: str | None, expected_code: str | None) -> None:
    result = classify_vxx_issue(**kwargs)
    assert result["failure_class"] == expected_class
    assert result["failure_code"] == expected_code


def test_massive_config_missing_vxx_is_symbol_contract(tmp_path: Path) -> None:
    original = json.loads((ROOT / "configs/m21/massive_stocks.json").read_text(encoding="utf-8"))
    original["symbols"] = [item for item in original["symbols"] if item["symbol"] != "VXX"]
    path = tmp_path / "massive-missing-vxx.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    config = M21Config.from_file(ROOT / "configs/m21/free_close.json")
    from dataclasses import replace

    config = replace(config, massive_stocks_config_path=str(path))
    with pytest.raises(M21SourceError) as exc_info:
        MassiveFreeStocksSource.from_config(config)
    assert exc_info.value.code == "CONFIG_MISSING_SYMBOL"
    assert exc_info.value.failure_class == "symbol_contract"
