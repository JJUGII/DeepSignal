from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import main as main_mod
from deepsignal.collector.market.market_data import MarketData
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import (
    init_database,
    insert_market_prices,
    insert_signal_result,
    save_real_account_snapshot,
)


def _has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def _seed_db(path: Path) -> None:
    db = str(init_database(str(path)))
    insert_signal_result(
        db,
        SignalResult(
            symbol="005930",
            signal_date="2026-05-17",
            technical_score=80.0,
            news_score=None,
            macro_score=None,
            final_score=80.0,
            action="BUY_CANDIDATE",
            confidence=0.8,
            reason="test",
            raw={},
        ),
    )
    insert_market_prices(
        db,
        [
            MarketData(
                symbol="005930",
                trade_date="2026-05-17",
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                adjusted_close=100.0,
                volume=1000,
                source="yfinance",
                raw={},
            )
        ],
    )
    save_real_account_snapshot(
        db,
        datetime.now().isoformat(timespec="seconds"),
        "kis",
        cash=10_000.0,
        withdrawable_cash=10_000.0,
        total_market_value=0.0,
        total_equity=10_000.0,
        raw_payload={},
    )


def test_ai_live_recommend_cli_writes_outputs(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed_db(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(["ai-live-recommend", "--broker", "kis", "--output-dir", str(out), "--capital-limit", "1000", "--max-recommendations", "5"])

    assert rc == 0
    assert (out / "AI_LIVE_TRADE_RECOMMENDATION.md").exists()
    rec_json = sorted(out.glob("ai_live_trade_recommendation_*.json"))
    plan_json = sorted(out.glob("live_order_plan_ai_*.json"))
    assert rec_json
    assert plan_json
    rec = json.loads(rec_json[-1].read_text(encoding="utf-8"))
    plan = json.loads(plan_json[-1].read_text(encoding="utf-8"))
    assert rec["recommendations"][0]["action"] == "BUY"
    assert plan["status"] == "PENDING_APPROVAL"
    assert plan["approval_required"] is True
    assert plan["orders"][0]["side"] == "BUY"
    assert plan["orders"][0]["order_type"] == "LIMIT"
    assert plan["safety_boundary"]["live_approve_called"] is False
    assert plan["safety_boundary"]["kis_order_cash_post_called"] is False
    assert not _has_key(rec["account_context"], "raw")
    md = (out / "AI_LIVE_TRADE_RECOMMENDATION.md").read_text(encoding="utf-8")
    assert "AI 실거래 추천 요약" in md
    assert "live-approve 연결 방식" in md


def test_ai_live_recommend_cli_filters_symbols(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed_db(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(["ai-live-recommend", "--broker", "kis", "--output-dir", str(out), "--symbols", "000660"])

    assert rc == 0
    rec_json = sorted(out.glob("ai_live_trade_recommendation_*.json"))[-1]
    rec = json.loads(rec_json.read_text(encoding="utf-8"))
    assert rec["recommendations"] == []
    assert rec["order_plan"]["orders"] == []


def test_ai_live_recommend_cli_does_not_require_network(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed_db(db)
    monkeypatch.setenv("DB_PATH", str(db))

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("KISBroker should not be constructed without --network")

    monkeypatch.setattr("deepsignal.live_trading.kis_broker.KISBroker", _forbidden)

    rc = main_mod.main(["ai-live-recommend", "--broker", "kis", "--output-dir", str(out)])

    assert rc == 0
