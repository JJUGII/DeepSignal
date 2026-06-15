"""KRX(국내주식) 호가가격단위 정렬 — 2023-01-25 KOSPI·KOSDAQ 통합 개편 반영.

버그(A1): 주문가가 호가단위에 안 맞으면 KIS가 APBK0506 "주식주문호가단위 오류"로
거부한다. auto_sell_executor가 STOP_LOSS 매도가를 현재가×(1-bps)로 만들고 브로커는
int(round())로 1원만 깎아 제출 → 30만원대(호가 500원) 종목에서 끝자리 28/750 같은
무효가격 → 손절 108회 거부 → 손실 종목이 계좌에 박힘.

해결: 제출 직전 호가단위 격자로 스냅. 체결성 보존을 위해 BUY=올림/SELL=내림.
"""

from __future__ import annotations

# (상한미만, 호가단위) — 가격이 edge 값이면 위 구간 적용(예: 정확히 200,000원 → 500원)
_KRX_TICKS: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (float("inf"), 1_000),
)


def krx_tick_size(price: float) -> int:
    """가격대별 KRX 호가단위(원)."""
    px = float(price)
    for upper, tick in _KRX_TICKS:
        if px < upper:
            return tick
    return 1_000


def round_krx_price(price: float, side: str = "") -> int:
    """가격을 KRX 호가단위 격자로 스냅한 정수(원).

    side=BUY  → 올림(ceil)  : 매수 체결성↑(살짝 비싸게)
    side=SELL → 내림(floor) : 매도 체결성↑(살짝 싸게, 손절 방어)
    그 외     → 반올림(nearest)
    호가단위가 바뀌는 경계로 스냅된 경우 한 번 더 보정(예: 5,000 경계).
    """
    px = float(price)
    if px <= 0:
        return 0
    tick = krx_tick_size(px)
    s = (side or "").strip().upper()
    if s == "BUY":
        snapped = int(-(-px // tick) * tick)          # ceil
    elif s == "SELL":
        snapped = int(px // tick * tick)              # floor
    else:
        snapped = int(round(px / tick) * tick)
    # 경계에서 tick이 달라졌으면 스냅값 기준 tick으로 한 번 더 정렬
    tick2 = krx_tick_size(snapped)
    if tick2 != tick and snapped % tick2 != 0:
        snapped = int(round(snapped / tick2) * tick2)
    return max(1, snapped)
