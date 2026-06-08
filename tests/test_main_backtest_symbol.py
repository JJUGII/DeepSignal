"""backtest-symbol CLI 스모크."""

from __future__ import annotations

from datetime import date, timedelta

import main as main_mod


def _fake_rows():
    base = date(2026, 1, 2)
    return [
        {
            "bar_time": (base + timedelta(days=i)).isoformat(),
            "close": 100.0 + i * 0.5,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
        }
        for i in range(40)
    ]


def test_main_backtest_symbol_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "btcli.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.storage import database as db_mod

    monkeypatch.setattr(
        db_mod,
        "fetch_market_prices",
        lambda *a, **k: _fake_rows(),
        raising=True,
    )

    main_mod.main(["backtest-symbol", "FAKE"])
    out = capsys.readouterr().out
    assert "DeepSignal backtest finished" in out
    assert "Include News: False" in out
    assert "Symbol: FAKE" in out
    assert "Strategy: technical_v1" in out
    assert "Saved:" in out


def test_main_backtest_symbol_include_news_flag(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "btcli2.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.storage import database as db_mod

    monkeypatch.setattr(
        db_mod,
        "fetch_market_prices",
        lambda *a, **k: _fake_rows(),
        raising=True,
    )

    main_mod.main(["backtest-symbol", "FAKE", "--include-news"])
    out = capsys.readouterr().out
    assert "Include News: True" in out
