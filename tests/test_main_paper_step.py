"""paper-step CLI 스모크."""

from __future__ import annotations

import main as main_mod
from deepsignal.paper_trading.paper_trading_engine import (
    PaperAccountSnapshot,
    PaperPosition,
)


def _fake_run_step(self, db_path, symbol):
    return PaperAccountSnapshot(
        snapshot_date="2026-05-13",
        cash=10000.0,
        equity=10150.0,
        positions_value=150.0,
        positions=[
            PaperPosition(
                symbol="FAKE",
                quantity=1,
                avg_price=100.0,
                last_price=150.0,
                market_value=150.0,
                unrealized_pnl=50.0,
                unrealized_pnl_pct=50.0,
            )
        ],
        last_action="HOLD",
        reason="테스트 스냅샷",
        raw={},
    )


def test_main_paper_step_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "pp.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.paper_trading import paper_trading_engine as pt_mod

    monkeypatch.setattr(pt_mod.PaperTradingEngine, "run_step", _fake_run_step)
    main_mod.main(["paper-step", "FAKE"])
    out = capsys.readouterr().out
    assert "DeepSignal paper trading step finished" in out
    assert "Symbol: FAKE" in out
    assert "Date: 2026-05-13" in out
    assert "Action: HOLD" in out
    assert "Reason:" in out
