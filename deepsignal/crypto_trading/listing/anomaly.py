"""Volume / price anomaly scoring for listing watch candidates."""

from __future__ import annotations

from typing import Any

from deepsignal.crypto_trading.broker.interface import CryptoTicker


def volume_ratio_from_candles(candles: list[dict[str, Any]], ticker: CryptoTicker) -> float | None:
    """24h KRW turnover vs trailing daily average (excludes today if in series)."""
    values: list[float] = []
    for c in candles:
        vol = float(c.get("candle_acc_trade_volume") or 0)
        px = float(c.get("trade_price") or c.get("opening_price") or 0)
        if vol > 0 and px > 0:
            values.append(vol * px)
    if len(values) < 3:
        return None
    # last row is often "today" in reversed series — use prior days for baseline
    baseline = values[:-1] if len(values) > 4 else values
    avg = sum(baseline) / len(baseline) if baseline else 0.0
    current = float(ticker.acc_trade_price_24h or 0)
    if avg <= 0 or current <= 0:
        return None
    return current / avg


def change_1h_pct_from_minute_candles(rows: list[dict[str, Any]]) -> float | None:
    if len(rows) < 2:
        return None
    sorted_rows = sorted(rows, key=lambda r: int(r.get("time") or 0))
    first = float(sorted_rows[0].get("close") or 0)
    last = float(sorted_rows[-1].get("close") or 0)
    if first <= 0:
        return None
    return (last - first) / first * 100.0


def compute_anomaly_score(
    *,
    vol_ratio: float | None,
    chg_24h_pct: float,
    chg_1h_pct: float | None,
    is_new: bool,
    new_on: str,
    vol_ratio_alert: float,
) -> tuple[float, list[str]]:
    score = 0.0
    tags: list[str] = []

    if vol_ratio is not None:
        if vol_ratio >= vol_ratio_alert * 1.5:
            score += 40.0
            tags.append("거래량급증")
        elif vol_ratio >= vol_ratio_alert:
            score += 28.0
            tags.append("거래량증가")

    if chg_24h_pct >= 15.0:
        score += 22.0
        tags.append("24h급등")
    elif chg_24h_pct >= 8.0:
        score += 12.0
        tags.append("24h상승")
    elif chg_24h_pct <= -10.0:
        score += 8.0
        tags.append("24h급락")

    if chg_1h_pct is not None:
        if chg_1h_pct >= 5.0:
            score += 18.0
            tags.append("1h급등")
        elif chg_1h_pct >= 2.5:
            score += 8.0
            tags.append("1h상승")

    if is_new:
        score += 15.0
        if new_on == "bithumb":
            tags.append("빗썸신규")
        elif new_on == "upbit":
            tags.append("업비트신규")
        else:
            tags.append("신규상장")

    if score >= 65 and "거래량급증" not in tags and vol_ratio and vol_ratio >= vol_ratio_alert:
        tags.append("주의")

    return min(100.0, score), tags
