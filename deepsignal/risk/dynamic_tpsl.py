"""동적 TP/SL 엔진 — ATR + 자산등급 + 시장상태 + 기대값(EV) 4단계 복합.

공식:
  TP = ATR% × grade_tp_mult × market_tp_mult × ev_mult
  SL = ATR% × grade_sl_mult × market_sl_mult

  (기본 → 등급 반영 → 시장 조정 → 고확신일수록 TP 확대)

EV ≤ 0 → blocked=True  (진입 차단 신호; 기존 포지션 청산에는 무관)

사용 예:
    from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_from_file

    bars = load_bars_from_file("/output/kis_stream/bars/005930_1m.jsonl")
    result = compute_dynamic_tpsl("005930", "kis_stock", bars)
    policy = RiskGuardPolicy(**result.as_policy_kwargs())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


# ═══════════════════════════════════════════════════════════════════════
# 1. 열거형
# ═══════════════════════════════════════════════════════════════════════

class AssetGrade(str, Enum):
    """자산 등급 (변동성·신뢰도 기준)."""
    A = "A"   # BTC, ETH, SPY, QQQ, 삼성전자, SK하이닉스
    B = "B"   # 주요 대형주, 우량 알트코인
    C = "C"   # 중소형주, 중형 알트
    D = "D"   # 잡코인, 3배 레버리지 ETF


class MarketState(str, Enum):
    """시장 상태."""
    TREND_UP   = "TREND_UP"    # 상승장
    SIDEWAYS   = "SIDEWAYS"    # 횡보
    TREND_DOWN = "TREND_DOWN"  # 하락장
    CRASH      = "CRASH"       # 폭락 (단봉 -5% 이상)


# ═══════════════════════════════════════════════════════════════════════
# 2. 등급 분류 기준
# ═══════════════════════════════════════════════════════════════════════

# A등급 심볼
_A_CRYPTO = frozenset({"KRW-BTC", "KRW-ETH", "BTCUSDT", "ETHUSDT"})

_A_KIS_DOMESTIC = frozenset({
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "003550",  # LG
    "066570",  # LG전자
    "012330",  # 현대모비스
    "028260",  # 삼성물산
    "017670",  # SK텔레콤
    "030200",  # KT
    "096770",  # SK이노베이션
    "068270",  # 셀트리온
    "207940",  # 삼성바이오로직스
})

_A_OVERSEAS = frozenset({
    "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
    "AMEX:SPY", "NASD:QQQ", "NASD:NVDA", "NASD:AAPL", "NASD:MSFT",
})

_B_OVERSEAS = frozenset({
    "META", "AMZN", "GOOGL", "TSLA", "AMD", "AVGO", "MU", "INTC",
    "JPM", "GS", "XOM", "TLT", "GLD", "SLV",
    "BABA", "JD", "PDD", "ONEQ",
    "NASD:META", "NASD:AMZN", "NASD:GOOGL", "NASD:TSLA",
    "NASD:AMD", "NASD:AVGO", "NASD:MU", "NASD:INTC",
    "NYSE:JPM", "NYSE:GS", "NYSE:XOM",
    "NYSE:TLT", "NYSE:GLD", "NYSE:SLV",
    "NYSE:BABA", "NASD:JD", "NASD:PDD", "NASD:ONEQ",
})

# D등급 — 3배 레버리지 ETF
_D_OVERSEAS = frozenset({
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
    "FNGU", "FNGD", "LABU", "LABD", "UVXY",
    "NASD:TQQQ", "NASD:SQQQ", "NYSE:SPXL", "NYSE:SPXS",
    "NYSE:SOXL", "NYSE:SOXS", "NASD:FNGU", "NASD:FNGD",
    "NYSE:LABU", "NYSE:LABD", "NYSE:UVXY",
})

# D등급 코인 패턴 (meme / 소형 알트)
_D_CRYPTO_SUBSTR = (
    "BOME", "DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI",
    "MEME", "BABYDOGE", "SATS", "RATS", "TURT",
)

# 바 데이터 없을 때 등급별 기본 ATR%
_DEFAULT_ATR_PCT: dict[tuple[str, AssetGrade], float] = {
    ("crypto",       AssetGrade.A): 2.0,
    ("crypto",       AssetGrade.B): 3.5,
    ("crypto",       AssetGrade.C): 5.0,
    ("crypto",       AssetGrade.D): 7.0,
    ("kis_stock",    AssetGrade.A): 1.2,
    ("kis_stock",    AssetGrade.B): 2.0,
    ("kis_stock",    AssetGrade.C): 3.0,
    ("kis_stock",    AssetGrade.D): 4.0,
    ("kis_overseas", AssetGrade.A): 1.8,
    ("kis_overseas", AssetGrade.B): 2.5,
    ("kis_overseas", AssetGrade.C): 3.5,
    ("kis_overseas", AssetGrade.D): 6.0,
}


def classify_grade(symbol: str, asset_class: str) -> AssetGrade:
    """심볼 + 자산 클래스 → A/B/C/D 등급."""
    sym    = str(symbol).strip().upper()
    ticker = sym.split(":")[-1]   # "NASD:NVDA" → "NVDA"

    if asset_class == "crypto":
        if sym in _A_CRYPTO:
            return AssetGrade.A
        sym_clean = sym.replace("KRW-", "").replace("USDT", "").replace("BTC", "")
        if any(p in sym_clean for p in _D_CRYPTO_SUBSTR):
            return AssetGrade.D
        # 이름 길이 긴 알트는 C, 짧은 건 B (휴리스틱)
        base = sym.replace("KRW-", "").replace("USDT", "")
        return AssetGrade.C if len(base) > 4 else AssetGrade.B

    elif asset_class == "kis_stock":
        if sym in _A_KIS_DOMESTIC:
            return AssetGrade.A
        # 숫자 코드가 아니면 ETF → C
        if not sym.isdigit():
            return AssetGrade.C
        return AssetGrade.B   # 일반 국내 종목 기본값

    elif asset_class == "kis_overseas":
        if sym in _D_OVERSEAS or ticker in _D_OVERSEAS:
            return AssetGrade.D
        if sym in _A_OVERSEAS or ticker in _A_OVERSEAS:
            return AssetGrade.A
        if sym in _B_OVERSEAS or ticker in _B_OVERSEAS:
            return AssetGrade.B
        return AssetGrade.C

    return AssetGrade.B


# ═══════════════════════════════════════════════════════════════════════
# 3. ATR 계산
# ═══════════════════════════════════════════════════════════════════════

def calc_atr_pct(
    bars: Sequence[dict[str, Any]],
    period: int = 14,
    timeframe_min: int = 1,
    asset_class: str = "kis_stock",
) -> float | None:
    """ATR을 **일간 스케일 %** 로 반환.

    단기봉(1m·15m)에서 계산된 ATR을 sqrt(봉수/일) 스케일링으로
    하루 기준 변동성으로 변환한다.

    국내주식 하루 = 390분(09:00~15:30)
    해외주식 하루 = 390분
    코인 하루     = 1440분(24h)
    """
    import math

    if len(bars) < period + 2:
        return None
    tr_list: list[float] = []
    for i in range(1, len(bars)):
        h  = float(bars[i].get("high")  or 0)
        lo = float(bars[i].get("low")   or 0)
        pc = float(bars[i - 1].get("close") or 0)
        if h <= 0 or lo <= 0 or pc <= 0:
            continue
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    raw_atr = sum(tr_list[-period:]) / period
    last_close = float(bars[-1].get("close") or 0)
    if last_close <= 0:
        return None

    raw_atr_pct = raw_atr / last_close * 100  # % per bar

    # 일간 스케일 변환: σ_daily ≈ σ_bar × sqrt(bars_per_day)
    if timeframe_min > 0:
        day_min = 1440 if asset_class == "crypto" else 390
        bars_per_day = day_min / timeframe_min
        scale = math.sqrt(bars_per_day)
        return raw_atr_pct * scale
    return raw_atr_pct


def _get_atr_pct(
    symbol: str,
    asset_class: str,
    bars: Sequence[dict[str, Any]] | None,
    grade: AssetGrade,
    period: int = 14,
    timeframe_min: int = 1,
) -> float:
    """ATR% 계산 (일간 스케일). 바 없거나 부족하면 등급별 기본값 사용."""
    if bars:
        v = calc_atr_pct(bars, period, timeframe_min=timeframe_min, asset_class=asset_class)
        if v and v > 0:
            # 과도한 스케일 방지: 등급별 최대값 클램프
            max_atr = {AssetGrade.A: 5.0, AssetGrade.B: 8.0,
                       AssetGrade.C: 12.0, AssetGrade.D: 20.0}.get(grade, 15.0)
            return min(v, max_atr)
    return _DEFAULT_ATR_PCT.get((asset_class, grade), 2.5)


# ═══════════════════════════════════════════════════════════════════════
# 4. 시장 상태 감지
# ═══════════════════════════════════════════════════════════════════════

def detect_market_state(bars: Sequence[dict[str, Any]]) -> MarketState:
    """최근 OHLCV 바로 시장 상태 분류 (MA5 vs MA20 + 급락 감지)."""
    closes = [float(b.get("close") or 0) for b in bars if b.get("close")]
    if len(closes) < 10:
        return MarketState.SIDEWAYS

    # 단봉 급락 감지 (최근 5봉)
    for i in range(max(1, len(closes) - 5), len(closes)):
        prev = closes[i - 1]
        if prev > 0 and (closes[i] - prev) / prev <= -0.05:
            return MarketState.CRASH

    ma5  = sum(closes[-5:]) / 5
    n20  = min(20, len(closes))
    ma20 = sum(closes[-n20:]) / n20

    ratio = (ma5 - ma20) / ma20 if ma20 > 0 else 0

    if ratio >  0.015:
        return MarketState.TREND_UP
    if ratio < -0.015:
        return MarketState.TREND_DOWN
    return MarketState.SIDEWAYS


# ═══════════════════════════════════════════════════════════════════════
# 5. 배수 테이블
# ═══════════════════════════════════════════════════════════════════════

# 등급별 기준 배수 (tp_mult, sl_mult) — 횡보장 기준
_GRADE_MULTS: dict[AssetGrade, tuple[float, float]] = {
    AssetGrade.A: (2.0, 1.0),   # 우량: TP=ATR×2, SL=ATR×1
    AssetGrade.B: (1.7, 0.9),
    AssetGrade.C: (1.4, 0.8),
    AssetGrade.D: (1.2, 0.7),   # 고위험: TP 타이트, SL 타이트
}

# 시장 상태별 보정 배수 (tp_adj, sl_adj)
_MARKET_ADJ: dict[MarketState, tuple[float, float]] = {
    MarketState.TREND_UP:   (1.25, 1.00),  # 상승장: TP 25% 확대
    MarketState.SIDEWAYS:   (1.00, 1.00),  # 횡보: 기본
    MarketState.TREND_DOWN: (0.80, 1.20),  # 하락장: TP 좁게, SL 타이트
    MarketState.CRASH:      (0.60, 1.50),  # 폭락: TP 매우 좁게, SL 강화
}

# 검증: Grade A + TREND_UP = 2.0 × 1.25 = 2.5
# BTC ATR=2%, TREND_UP, no EV:
#   TP = 2% × 2.0 × 1.25 × 1.0 = 5.0%
# BTC ATR=2%, TREND_UP, EV↑(ev_mult=1.3):
#   TP = 2% × 2.0 × 1.25 × 1.3 = 6.5%  ← 사용자 제시 예시와 일치


# ═══════════════════════════════════════════════════════════════════════
# 6. 기대값(EV) 계산
# ═══════════════════════════════════════════════════════════════════════

def calc_ev(win_prob: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """기대값 (EV) = P(win)×win% − P(loss)×loss%  (단위: %)."""
    return win_prob * abs(avg_win_pct) - (1.0 - win_prob) * abs(avg_loss_pct)


def _ev_mult(ev: float | None) -> float:
    """EV → TP 배수 (높을수록 TP 넓게)."""
    if ev is None:
        return 1.0
    if ev >= 4.0:
        return 1.35
    if ev >= 2.5:
        return 1.20
    if ev >= 1.0:
        return 1.10
    if ev >= 0.0:
        return 1.00
    return 0.85   # EV < 0: TP 축소 (blocked 플래그도 세움)


# ═══════════════════════════════════════════════════════════════════════
# 7. 최종 TP/SL 결과 & 계산 함수
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DynamicTpSl:
    """compute_dynamic_tpsl() 반환값."""
    symbol:        str
    asset_class:   str
    atr_pct:       float          # ATR % (예: 2.0 = 2%)
    grade:         AssetGrade
    market_state:  MarketState
    ev:            float | None   # 기대값 (없으면 None)
    tp_pct:        float          # 최종 TP (소수, 예: 0.05 = 5%)
    sl_pct:        float          # 최종 SL (소수, 예: -0.02 = -2%)
    warn_profit:   float          # 경고선 TP (tp_pct × 0.70)
    warn_loss:     float          # 경고선 SL (sl_pct × 0.60)
    blocked:       bool = False   # EV ≤ 0 → 진입 차단 권고
    detail:        dict = field(default_factory=dict)

    # ── RiskGuardPolicy 연계 ────────────────────────────────────────────

    def as_policy_kwargs(self) -> dict[str, float]:
        """RiskGuardPolicy(**result.as_policy_kwargs()) 로 사용."""
        return {
            "stop_loss_pct":   self.sl_pct,
            "take_profit_pct": self.tp_pct,
            "warn_loss_pct":   self.warn_loss,
            "warn_profit_pct": self.warn_profit,
        }

    # ── 텔레그램·로그용 요약 ────────────────────────────────────────────

    def summary_str(self) -> str:
        blocked_tag = " ⛔blocked" if self.blocked else ""
        return (
            f"{self.symbol} [{self.grade.value}등급/{self.market_state.value}] "
            f"ATR={self.atr_pct:.1f}% "
            f"TP=+{self.tp_pct * 100:.1f}% SL={self.sl_pct * 100:.1f}%"
            f"{blocked_tag}"
        )


def compute_dynamic_tpsl(
    symbol: str,
    asset_class: str,
    bars: Sequence[dict[str, Any]] | None = None,
    *,
    timeframe_min: int = 1,              # 바 타임프레임 (분 단위)
    win_prob:     float | None = None,
    avg_win_pct:  float | None = None,   # 소수 (0.06 = 6%)
    avg_loss_pct: float | None = None,   # 소수, 양수 입력 (0.03 = 3%)
    period: int = 14,
) -> DynamicTpSl:
    """동적 TP/SL 계산.

    Args:
        symbol:       종목 코드 ("005930", "KRW-BTC", "NASD:NVDA")
        asset_class:  "crypto" | "kis_stock" | "kis_overseas"
        bars:         OHLCV dict 리스트 (없으면 기본 ATR 사용)
        win_prob:     AI 승률 예측 (0.0 ~ 1.0)
        avg_win_pct:  AI 예상 평균 수익률 (소수, 없으면 None)
        avg_loss_pct: AI 예상 평균 손실률 (소수 양수, 없으면 None)
        period:       ATR 계산 봉 수 (기본 14)

    Returns:
        DynamicTpSl 인스턴스
    """
    grade = classify_grade(symbol, asset_class)
    atr   = _get_atr_pct(symbol, asset_class, bars, grade, period, timeframe_min)
    mkt   = detect_market_state(bars) if bars and len(bars) >= 10 else MarketState.SIDEWAYS

    # EV 계산 (AI 예측값 있을 때만)
    ev: float | None = None
    if win_prob is not None and avg_win_pct is not None and avg_loss_pct is not None:
        ev = calc_ev(win_prob, avg_win_pct * 100, avg_loss_pct * 100)

    g_tp, g_sl    = _GRADE_MULTS[grade]
    m_tp, m_sl    = _MARKET_ADJ[mkt]
    ev_m          = _ev_mult(ev)

    # ATR × 배수 = % 단위 결과
    raw_tp_pct = atr * g_tp * m_tp * ev_m    # e.g. 5.0 (%)
    raw_sl_pct = atr * g_sl * m_sl           # e.g. 2.0 (%)

    # 소수 변환 + 클램프
    tp = max(0.005, min(raw_tp_pct / 100.0, 0.30))    # 0.5% ~ 30%
    sl = max(-0.25,    -(raw_sl_pct / 100.0))          # 최대 -25%
    sl = min(sl, -0.003)                               # 최소 -0.3%

    warn_p = tp * 0.70
    warn_l = sl * 0.60

    return DynamicTpSl(
        symbol=symbol,
        asset_class=asset_class,
        atr_pct=round(atr, 3),
        grade=grade,
        market_state=mkt,
        ev=ev,
        tp_pct=round(tp, 5),
        sl_pct=round(sl, 5),
        warn_profit=round(warn_p, 5),
        warn_loss=round(warn_l, 5),
        blocked=(ev is not None and ev <= 0),
        detail={
            "grade_tp_mult":  g_tp,
            "grade_sl_mult":  g_sl,
            "market_tp_adj":  m_tp,
            "market_sl_adj":  m_sl,
            "ev_mult":        ev_m,
            "raw_tp_pct":     round(raw_tp_pct, 3),
            "raw_sl_pct":     round(raw_sl_pct, 3),
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# 8. 바 데이터 로더 (편의 함수)
# ═══════════════════════════════════════════════════════════════════════

def load_bars_from_file(path: str | Path, last_n: int = 60) -> list[dict[str, Any]]:
    """JSONL 바 파일에서 최근 N봉 로드."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        result = []
        for line in lines[-last_n:]:
            line = line.strip()
            if line and not line.startswith("._"):
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result
    except Exception:
        return []


_TIMEFRAME_MIN: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}

# 자산별 선호 타임프레임: 일간 ATR 계산에 적합한 봉 크기
_PREFERRED_TIMEFRAME: dict[str, list[str]] = {
    "kis_stock":    ["15m", "5m", "1m"],   # 15m 우선
    "kis_overseas": ["15m", "5m", "1m"],
    "crypto":       ["15m", "5m", "1m"],
}


def find_bar_file(
    symbol: str,
    asset_class: str,
    project_root: str | Path,
    timeframe: str | None = None,
) -> tuple[Path, str] | tuple[None, None]:
    """자산 클래스별 바 파일 탐색. (path, timeframe) 반환."""
    root   = Path(project_root)
    ticker = str(symbol).split(":")[-1].upper()
    sym    = str(symbol).upper()

    # 탐색할 타임프레임 순서 결정
    tfs = [timeframe] if timeframe else _PREFERRED_TIMEFRAME.get(asset_class, ["15m", "1m"])

    for tf in tfs:
        candidates: list[Path] = []
        if asset_class == "kis_stock":
            candidates = [
                root / "output"  / "kis_stream" / "bars" / f"{sym}_{tf}.jsonl",
                root / "outputs" / "kis_stream" / "bars" / f"{sym}_{tf}.jsonl",
            ]
        elif asset_class == "kis_overseas":
            candidates = [
                root / "output"  / "kis_stream" / "kis_overseas" / "bars" / f"{ticker}_{tf}.jsonl",
                root / "outputs" / "kis_stream" / "kis_overseas" / "bars" / f"{ticker}_{tf}.jsonl",
                root / "output"  / "kis_overseas" / "bars" / f"{ticker}_{tf}.jsonl",
            ]
        elif asset_class == "crypto":
            candidates = [
                root / "outputs" / "binance_stream" / "bars" / f"{sym}_{tf}.jsonl",
                root / "outputs" / "binance_stream" / "bars" / f"{ticker}_{tf}.jsonl",
            ]
        for p in candidates:
            if p.exists():
                return p, tf
    return None, None


def load_bars_for_symbol(
    symbol: str,
    asset_class: str,
    project_root: str | Path,
    timeframe: str | None = None,
    last_n: int = 60,
) -> tuple[list[dict[str, Any]], int]:
    """심볼 → (bars, timeframe_min) 반환. 없으면 ([], 1)."""
    path, tf = find_bar_file(symbol, asset_class, project_root, timeframe)
    if path is None:
        return [], 1
    bars = load_bars_from_file(path, last_n)
    tf_min = _TIMEFRAME_MIN.get(tf or "1m", 1)
    return bars, tf_min
