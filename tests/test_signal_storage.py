"""signals 저장 테스트."""

from __future__ import annotations

from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import init_database, insert_signal_result


def _sample_result(symbol: str = "SIG", date: str = "2024-02-01") -> SignalResult:
    return SignalResult(
        symbol=symbol,
        signal_date=date,
        technical_score=10.0,
        news_score=None,
        macro_score=None,
        final_score=10.0,
        action="HOLD",
        confidence=0.1,
        reason="테스트",
        raw={"k": 1},
        strategy_name="technical_v1",
    )


def test_duplicate_signal_skipped(tmp_path) -> None:
    db = tmp_path / "sig.db"
    init_database(str(db))
    r = _sample_result()
    s1 = insert_signal_result(str(db), r)
    assert s1["inserted"] == 1
    s2 = insert_signal_result(str(db), r)
    assert s2["inserted"] == 0
    assert s2["skipped"] == 1
