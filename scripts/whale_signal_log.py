"""고래체결(whale) 신호 로거 — 매수 의사결정 시점의 whale_trade_ratio + scalping
서브스코어를 경량 JSONL로 누적한다. 이후 whale_signal_validate.py가 포워드
수익률과 조인해 IC(예측력)를 측정한다.

설계 의도
  - whale_trade_ratio(>20x면 scalping_scorer가 +3pt 휴리스틱 가산)가 *이후*
    수익을 실제로 예측하는지 검증할 데이터가 현재 없다(crypto_trades.db에는
    완결 실거래 112건뿐, 고래≥20x는 14건). 결정 시점마다 신호를 남겨야 표본이
    누적된다. 뉴스촉매(news_scores) 패턴과 동일한 채점→포워드 검증 구조.

사용 (라이브 배선은 사용자가 별도 수행 — 이 스크립트는 로깅 함수/CLI만 제공):
  # 라이브 결정 루프에서 import 해 호출(권장):
  from scripts.whale_signal_log import log_whale_decision
  log_whale_decision(symbol, feats_dict, ref_price=last_price)

  # 또는 단독 스냅샷 1회(현재 가동중인 코인 유니버스 전체 자동 채점):
  ./.venv/bin/python scripts/whale_signal_log.py --snapshot

출력: output/crypto_stream/WHALE_SIGNAL_LOG.jsonl (한 줄=한 결정)
  {ts, symbol, ref_price, whale_ratio, whale_pts, tradeflow_score, total_score,
   taker_buy_ratio, large_trade_count_1m, trade_acceleration, ...}
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LOG_PATH = Path(__file__).resolve().parents[1] / "output" / "crypto_stream" / "WHALE_SIGNAL_LOG.jsonl"

# scalping_scorer.py의 +3pt/+1.5pt 휴리스틱과 동일한 임계 (검증 대상)
_WHALE_HI = 20.0   # > 20x → +3pt
_WHALE_LO = 10.0   # > 10x → +1.5pt

# 함께 남겨 비교할 trade-flow 서브스코어 구성 피처
_EXTRA_FEATS = (
    "taker_buy_ratio",
    "large_trade_count_1m",
    "trade_acceleration",
    "buy_sell_delta",
    "volume_ratio_1m",
    "quote_vol_spike_5m",
    "breakout_score",
    "relative_strength_rank",
)


def _whale_pts(ratio: float) -> float:
    """현재 라이브에서 적용중인 고래 가중치(휴리스틱)를 그대로 재현."""
    if ratio != ratio:  # NaN
        return 0.0
    if ratio > _WHALE_HI:
        return 3.0
    if ratio > _WHALE_LO:
        return 1.5
    return 0.0


def log_whale_decision(
    symbol: str,
    feats: dict[str, float],
    ref_price: float | None = None,
    *,
    log_path: Path | str = _LOG_PATH,
) -> dict:
    """결정 시점의 고래·트레이드플로 신호를 JSONL 한 줄로 누적. 기록한 dict 반환.

    feats: FeatureEngine.feature_dict(symbol) 결과(이름→값). whale_trade_ratio 포함.
    ref_price: 결정 시점 기준가(없으면 None — 검증은 entry_time 부근 market_prices로 대체).
    비치명: 어떤 예외도 로깅을 막지 않도록 호출측에서 try/except 권장.
    """
    from deepsignal.crypto_trading.signal.scalping_scorer import (
        _tradeflow_score,
        compute_scalping_score,
    )

    whale = float(feats.get("whale_trade_ratio", float("nan")))
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "ref_price": float(ref_price) if ref_price is not None else None,
        "whale_ratio": None if whale != whale else whale,
        "whale_pts": _whale_pts(whale),
    }

    # 서브스코어(비치명) — 라이브 점수 산식 그대로
    try:
        rec["tradeflow_score"] = round(_tradeflow_score(feats, []), 3)
    except Exception:
        rec["tradeflow_score"] = None
    try:
        # compute_scalping_score(symbol, feats) — 라이브와 동일 시그니처
        _sc = compute_scalping_score(symbol, feats)
        rec["total_score"] = round(float(getattr(_sc, "total", getattr(_sc, "score", float("nan")))), 3)
    except Exception:
        rec["total_score"] = None

    for f in _EXTRA_FEATS:
        v = feats.get(f, float("nan"))
        rec[f] = None if v != v else round(float(v), 6)

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def snapshot_universe() -> int:
    """현재 가동중인 라이브 FeatureEngine 상태에서 코인 유니버스 전체를 1회 채점·로깅.

    라이브 엔진 핸들이 없는 단독 실행에서는 outputs/CRYPTO_UNIVERSE_SNAPSHOT.json의
    심볼 목록을 읽어 last_price만 ref로 남긴다(피처 미가용 시 whale=None 기록).
    실제 whale_ratio는 라이브 결정루프에서 log_whale_decision()을 직접 호출해야
    채워진다 — 이 함수는 하니스 배선 전 표본이 안 쌓이는 상황의 플레이스홀더.
    """
    snap = Path(__file__).resolve().parents[1] / "outputs" / "CRYPTO_UNIVERSE_SNAPSHOT.json"
    if not snap.exists():
        print(f"유니버스 스냅샷 없음: {snap}")
        return 0
    try:
        data = json.loads(snap.read_text())
    except Exception as exc:
        print(f"스냅샷 파싱 실패: {exc}")
        return 0
    symbols = data.get("symbols") or data.get("universe") or []
    if isinstance(symbols, dict):
        symbols = list(symbols.keys())
    n = 0
    for sym in symbols:
        # 피처 미가용 — 자리표시 레코드(라이브 배선 전 표본 부재를 명시적으로 남김)
        log_whale_decision(str(sym), {}, ref_price=None)
        n += 1
    print(f"스냅샷 {n}건 기록(피처 미가용 자리표시 — 실 신호는 라이브 훅 필요).")
    return n


def main() -> None:
    if "--snapshot" in sys.argv:
        snapshot_universe()
        return
    print(__doc__)
    print(f"\n현재 로그: {_LOG_PATH}")
    if _LOG_PATH.exists():
        nlines = sum(1 for _ in _LOG_PATH.open())
        print(f"누적 {nlines}건. whale_signal_validate.py로 IC 검증.")
    else:
        print("아직 로그 없음 — 라이브 결정루프에서 log_whale_decision() 호출 필요.")


if __name__ == "__main__":
    main()
