"""collect-macro / analyze-macro CLI 스모크."""

from __future__ import annotations

import main as main_mod
from deepsignal.collector.economic.economic_collector import EconomicIndicator
from deepsignal.collector.economic import economic_collector as ec_mod


def _fake_collect_macro(self) -> list[EconomicIndicator]:
    return [
        EconomicIndicator("VIX", "2026-05-01", 14.0, "yfinance", {}),
        EconomicIndicator("DXY", "2026-05-01", 99.0, "yfinance", {}),
        EconomicIndicator("TNX", "2026-05-01", 2.9, "yfinance", {}),
    ]


def test_main_collect_macro_inserts(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "m.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setattr(
        ec_mod.EconomicCollector,
        "collect_macro_indicators",
        _fake_collect_macro,
        raising=True,
    )
    main_mod.main(["collect-macro"])
    out = capsys.readouterr().out
    assert "DeepSignal macro indicators collection finished" in out
    assert "Inserted: 3" in out


def test_main_analyze_macro_output(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "m2.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setattr(
        ec_mod.EconomicCollector,
        "collect_macro_indicators",
        _fake_collect_macro,
        raising=True,
    )
    main_mod.main(["collect-macro"])
    main_mod.main(["analyze-macro"])
    out = capsys.readouterr().out
    assert "DeepSignal macro analysis finished" in out
    assert "Macro Score:" in out
    assert "Market Regime:" in out
    assert "Confidence:" in out
    assert "Reason:" in out
