"""paper-rebalance CLI 스모크."""

from __future__ import annotations

import main as main_mod


def _fake_paper_rebalance_to_db(path_str, settings, **kwargs):
    _ = path_str, settings, kwargs
    print("DeepSignal paper rebalance finished")
    print("Date: 2026-05-13")
    print("Cash: 3200.00")
    print("Equity: 10050.00")
    print("Positions Value: 6850.00")
    print("Trades:")
    print("BUY AAPL qty=10 price=190.20")
    print("SELL TSLA qty=2 price=180.00")
    return {"outcome": "success", "trades": []}


def test_main_paper_rebalance_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "pp.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.pipelines import daily_pipeline as dp_mod

    monkeypatch.setattr(dp_mod, "paper_rebalance_to_db", _fake_paper_rebalance_to_db)
    main_mod.main(["paper-rebalance"])
    out = capsys.readouterr().out
    assert "DeepSignal paper rebalance finished" in out
    assert "Date: 2026-05-13" in out
    assert "Cash: 3200.00" in out
    assert "Equity: 10050.00" in out
    assert "Positions Value: 6850.00" in out
    assert "BUY AAPL qty=10 price=190.20" in out
    assert "SELL TSLA qty=2 price=180.00" in out


def test_main_paper_rebalance_cli_cost_args(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake(path_str, settings, *, rebalance_config=None, **_k):
        _ = path_str, settings
        captured["cfg"] = rebalance_config
        return {"outcome": "success", "trades": []}

    db = tmp_path / "pp.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.pipelines import daily_pipeline as dp_mod

    monkeypatch.setattr(dp_mod, "paper_rebalance_to_db", fake)
    main_mod.main(
        [
            "paper-rebalance",
            "--commission-rate",
            "0.002",
            "--slippage-rate",
            "0.003",
            "--min-trade-value",
            "25",
            "--rebalance-threshold",
            "0.02",
        ]
    )
    cfg = captured.get("cfg")
    assert cfg is not None
    assert cfg.commission_rate == 0.002
    assert cfg.slippage_rate == 0.003
    assert cfg.min_trade_value == 25.0
    assert cfg.rebalance_threshold == 0.02
