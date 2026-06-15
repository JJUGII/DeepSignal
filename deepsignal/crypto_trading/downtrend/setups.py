"""[Phase3] 하락장 반등 셋업 3종 (순수 판정 함수).

하락장에서 '아무 코인이나 롱'이 아니라, 통계적으로 짧은 반등 확률이 높은 특정 셋업만
허용한다. 전부 순수 함수 — SetupFeatures만으로 True/False. 실거래 전 Phase5 검증 통과 필수
(이 셋업들은 가설이며, 대부분 검증에서 탈락할 수 있음).

⚠️ 임계값은 합리적 초기값일 뿐 — 백테스트로 종목군·시장별 재튜닝 대상.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SetupFeatures:
    """반등 셋업 판정 입력. (없는 값은 기본=미충족 방향)"""

    # capitulation bounce
    drop_3m_pct: float = 0.0        # 직전 3분 수익률(%) — 음수가 급락
    atr_1m_pct: float = 0.0         # 1분 ATR(%)
    lower_wick_ratio: float = 0.0   # 현재 봉 아래꼬리 비율 (0~1)
    close: float = 0.0              # 현재 종가
    prev_candle_high: float = 0.0   # 직전 봉 고가
    volume_ratio_1m: float = 0.0    # 현재 1분 거래량 / 최근 평균
    vwap_1m: float = 0.0            # 1분 VWAP
    # relative strength reclaim
    coin_ret_5m_pct: float = 0.0
    btc_ret_5m_pct: float = 0.0
    coin_vwap_5m: float = 0.0
    btc_breaking_new_low: bool = True   # 기본 True(보수): BTC가 신저점 갱신 중이면 미충족
    # vwap reclaim
    was_below_vwap_recently: bool = False


def capitulation_bounce(
    f: SetupFeatures,
    *,
    drop_atr_mult: float = -1.2,
    min_wick: float = 0.45,
    min_vol_ratio: float = 2.0,
) -> bool:
    """급락 후 아래꼬리 + 직전고점 회복 + 거래량 폭발 + VWAP 복귀."""
    if f.atr_1m_pct <= 0 or f.close <= 0:
        return False
    drop_ok = f.drop_3m_pct <= drop_atr_mult * f.atr_1m_pct      # 급락 ≤ -1.2×ATR
    wick_ok = f.lower_wick_ratio >= min_wick
    reclaim_high = f.prev_candle_high > 0 and f.close > f.prev_candle_high
    vol_ok = f.volume_ratio_1m >= min_vol_ratio
    above_vwap = f.vwap_1m > 0 and f.close > f.vwap_1m
    return bool(drop_ok and wick_ok and reclaim_high and vol_ok and above_vwap)


def relative_strength_reclaim(
    f: SetupFeatures,
    *,
    min_rs_pct: float = 0.7,
) -> bool:
    """코인이 BTC보다 덜 빠지고(또는 더 오르고) VWAP 위, BTC는 신저점 안 깸."""
    if f.close <= 0:
        return False
    rs = f.coin_ret_5m_pct - f.btc_ret_5m_pct
    rs_ok = rs >= min_rs_pct
    above_vwap = f.coin_vwap_5m > 0 and f.close > f.coin_vwap_5m
    btc_ok = not f.btc_breaking_new_low
    return bool(rs_ok and above_vwap and btc_ok)


def vwap_reclaim_scalp(
    f: SetupFeatures,
    *,
    min_vol_ratio: float = 1.5,
) -> bool:
    """단기 VWAP 하향이탈 후 재돌파 + 거래량 동반."""
    if f.close <= 0 or f.vwap_1m <= 0:
        return False
    reclaimed = f.was_below_vwap_recently and f.close > f.vwap_1m
    vol_ok = f.volume_ratio_1m >= min_vol_ratio
    return bool(reclaimed and vol_ok)


_SETUPS = {
    "capitulation_bounce": capitulation_bounce,
    "relative_strength_reclaim": relative_strength_reclaim,
    "vwap_reclaim_scalp": vwap_reclaim_scalp,
}


def detect_setups(f: SetupFeatures) -> list[str]:
    """발화한 셋업 이름 목록. 비어있으면 하락장 신규매수 불가."""
    return [name for name, fn in _SETUPS.items() if fn(f)]
