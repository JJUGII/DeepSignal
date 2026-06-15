"""Cross-exchange listing watch scanner."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.broker.factory import load_crypto_broker
from deepsignal.crypto_trading.listing.anomaly import (
    change_1h_pct_from_minute_candles,
    compute_anomaly_score,
    volume_ratio_from_candles,
)
from deepsignal.crypto_trading.listing.config import (
    LISTING_WATCH_JSON,
    ListingWatchConfig,
)
from deepsignal.crypto_trading.listing.models import ListingCandidate, ListingScanResult
from deepsignal.crypto_trading.listing.notify import maybe_send_listing_alerts
from deepsignal.crypto_trading.listing.snapshot import (
    diff_new_markets,
    load_market_snapshot,
    save_market_snapshot,
)
from deepsignal.crypto_trading.signal.universe import fetch_tickers_batched, list_krw_markets


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _display_name(market: str, names: dict[str, str]) -> str:
    ko = names.get(market, "").strip()
    sym = market.replace("KRW-", "")
    return f"{ko} ({sym})" if ko else sym


def _deep_scan_market(
    broker: Any,
    market: str,
    ticker: Any,
) -> tuple[float | None, float | None]:
    vol_ratio: float | None = None
    chg_1h: float | None = None
    try:
        candles = broker.get_daily_candles(market, count=10)
        vol_ratio = volume_ratio_from_candles(candles, ticker)
    except Exception:
        pass
    if hasattr(broker, "get_minute_candles"):
        try:
            rows = broker.get_minute_candles(market, 1, 60)
            chg_1h = change_1h_pct_from_minute_candles(rows)
        except Exception:
            pass
    return vol_ratio, chg_1h


def run_listing_scan(
    output_dir: str | Path,
    *,
    cfg: ListingWatchConfig | None = None,
    network: bool = True,
    send_alerts: bool = True,
) -> ListingScanResult:
    """Scan Upbit/Bithumb market sets and score bithumb-only anomalies."""
    cfg = cfg or ListingWatchConfig.from_env()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not network:
        cached = load_listing_watch_latest(out)
        if cached:
            return _dict_to_result(cached)
        return ListingScanResult(
            generated_at=_iso_now(),
            upbit_total=0,
            bithumb_total=0,
            both_count=0,
            bithumb_only_count=0,
            upbit_only_count=0,
            new_on_bithumb=[],
            new_on_upbit=[],
            candidates=[],
        )

    upbit = load_crypto_broker("upbit", dry_run=False)
    bithumb = load_crypto_broker("bithumb", dry_run=False)

    upbit_markets, upbit_names = list_krw_markets(upbit)
    bithumb_markets, bithumb_names = list_krw_markets(bithumb)
    up_set = set(upbit_markets)
    bi_set = set(bithumb_markets)
    both = up_set & bi_set
    bithumb_only = sorted(bi_set - up_set)
    upbit_only = sorted(up_set - bi_set)
    all_names = {**upbit_names, **bithumb_names}

    prev = load_market_snapshot(out)
    prev_up = set(prev.get("upbit_markets") or []) if prev else None
    prev_bi = set(prev.get("bithumb_markets") or []) if prev else None
    new_on_bithumb = diff_new_markets(bi_set, prev_bi)
    new_on_upbit = diff_new_markets(up_set, prev_up)
    new_bi_set = set(new_on_bithumb)
    new_up_set = set(new_on_upbit)

    save_market_snapshot(out, upbit=up_set, bithumb=bi_set)

    tickers = fetch_tickers_batched(
        bithumb,
        bithumb_only,
        batch_size=100,
        valid_markets=bi_set,
    )

    filtered: list[tuple[str, Any]] = []
    for m in bithumb_only:
        t = tickers.get(m)
        if t is None:
            continue
        acc = float(getattr(t, "acc_trade_price_24h", 0) or 0)
        if acc < cfg.min_acc_trade_24h:
            continue
        filtered.append((m, t))

    filtered.sort(key=lambda x: float(getattr(x[1], "acc_trade_price_24h", 0) or 0), reverse=True)
    deep_targets = filtered[: cfg.max_deep_scan]

    candidates: list[ListingCandidate] = []
    for market, ticker in deep_targets:
        vol_ratio, chg_1h = _deep_scan_market(bithumb, market, ticker)
        chg_24h = float(getattr(ticker, "signed_change_rate", 0) or 0) * 100.0
        is_new = market in new_bi_set
        score, tags = compute_anomaly_score(
            vol_ratio=vol_ratio,
            chg_24h_pct=chg_24h,
            chg_1h_pct=chg_1h,
            is_new=is_new,
            new_on="bithumb" if is_new else "",
            vol_ratio_alert=cfg.vol_ratio_alert,
        )
        candidates.append(
            ListingCandidate(
                market=market,
                display_name=_display_name(market, all_names),
                exchange_side="bithumb_only",
                trade_price=float(getattr(ticker, "trade_price", 0) or 0),
                signed_change_rate=chg_24h,
                acc_trade_price_24h=float(getattr(ticker, "acc_trade_price_24h", 0) or 0),
                vol_ratio=vol_ratio,
                chg_1h_pct=chg_1h,
                anomaly_score=score,
                tags=tags,
                is_new=is_new,
                new_on="bithumb" if is_new else "",
            )
        )

    # Upbit-only / new on upbit — lighter rows (ticker only, no deep scan)
    if upbit_only:
        up_tickers = fetch_tickers_batched(
            upbit,
            upbit_only[: min(40, len(upbit_only))],
            batch_size=100,
            valid_markets=up_set,
        )
        for market in sorted(up_tickers.keys(), key=lambda m: float(getattr(up_tickers[m], "acc_trade_price_24h", 0) or 0), reverse=True)[:20]:
            ticker = up_tickers[market]
            acc = float(getattr(ticker, "acc_trade_price_24h", 0) or 0)
            if acc < cfg.min_acc_trade_24h * 0.5:
                continue
            chg_24h = float(getattr(ticker, "signed_change_rate", 0) or 0) * 100.0
            is_new = market in new_up_set
            score, tags = compute_anomaly_score(
                vol_ratio=None,
                chg_24h_pct=chg_24h,
                chg_1h_pct=None,
                is_new=is_new,
                new_on="upbit" if is_new else "",
                vol_ratio_alert=cfg.vol_ratio_alert,
            )
            if score < 20 and not is_new:
                continue
            candidates.append(
                ListingCandidate(
                    market=market,
                    display_name=_display_name(market, all_names),
                    exchange_side="upbit_only",
                    trade_price=float(getattr(ticker, "trade_price", 0) or 0),
                    signed_change_rate=chg_24h,
                    acc_trade_price_24h=acc,
                    anomaly_score=score,
                    tags=tags,
                    is_new=is_new,
                    new_on="upbit" if is_new else "",
                )
            )

    candidates.sort(key=lambda c: (c.anomaly_score, c.acc_trade_price_24h), reverse=True)

    alerts_sent: list[str] = []
    if send_alerts:
        alerts_sent = maybe_send_listing_alerts(out, candidates, cfg)

    result = ListingScanResult(
        generated_at=_iso_now(),
        upbit_total=len(up_set),
        bithumb_total=len(bi_set),
        both_count=len(both),
        bithumb_only_count=len(bithumb_only),
        upbit_only_count=len(upbit_only),
        new_on_bithumb=new_on_bithumb,
        new_on_upbit=new_on_upbit,
        candidates=candidates,
        alerts_sent=alerts_sent,
    )

    path = out / LISTING_WATCH_JSON
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def load_listing_watch_latest(output_dir: str | Path) -> dict[str, Any] | None:
    path = Path(output_dir) / LISTING_WATCH_JSON
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _dict_to_result(doc: dict[str, Any]) -> ListingScanResult:
    cands = []
    for row in doc.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        cands.append(
            ListingCandidate(
                market=str(row.get("market") or ""),
                display_name=str(row.get("display_name") or ""),
                exchange_side=str(row.get("exchange_side") or ""),
                trade_price=float(row.get("trade_price") or 0),
                signed_change_rate=float(row.get("signed_change_rate") or 0),
                acc_trade_price_24h=float(row.get("acc_trade_price_24h") or 0),
                vol_ratio=row.get("vol_ratio"),
                chg_1h_pct=row.get("chg_1h_pct"),
                anomaly_score=float(row.get("anomaly_score") or 0),
                tags=list(row.get("tags") or []),
                is_new=bool(row.get("is_new")),
                new_on=str(row.get("new_on") or ""),
            )
        )
    return ListingScanResult(
        generated_at=str(doc.get("generated_at") or ""),
        upbit_total=int(doc.get("upbit_total") or 0),
        bithumb_total=int(doc.get("bithumb_total") or 0),
        both_count=int(doc.get("both_count") or 0),
        bithumb_only_count=int(doc.get("bithumb_only_count") or 0),
        upbit_only_count=int(doc.get("upbit_only_count") or 0),
        new_on_bithumb=list(doc.get("new_on_bithumb") or []),
        new_on_upbit=list(doc.get("new_on_upbit") or []),
        candidates=cands,
        alerts_sent=list(doc.get("alerts_sent") or []),
        disclaimer=str(doc.get("disclaimer") or ""),
    )
