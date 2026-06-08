"""Build (X, y, timestamps) from Binance 1m bar jsonl via FeatureEngine replay."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from deepsignal.market_data.binance_stream.models import OhlcvBar
from deepsignal.market_data.feature_engine.engine import FeatureEngine
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig


def load_bars_jsonl(path: Path) -> list[OhlcvBar]:
    bars: list[OhlcvBar] = []
    if not path.is_file():
        return bars
    # macOS AppleDouble sidecar files (._*) share the .jsonl suffix but are
    # binary metadata — skip them and any file we cannot decode as UTF-8.
    if path.name.startswith("._"):
        return bars
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return bars
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            bars.append(
                OhlcvBar(
                    symbol=str(raw["symbol"]),
                    timeframe=str(raw.get("timeframe") or "1m"),
                    open_ts_ms=int(raw["open_ts_ms"]),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw.get("volume") or 0),
                    quote_volume=float(raw.get("quote_volume") or 0),
                    trade_count=int(raw.get("trade_count") or 0),
                    closed=bool(raw.get("closed", True)),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda b: b.open_ts_ms)
    return bars


def aggregate_higher_tf(bars_1m: list[OhlcvBar], minutes: int) -> list[OhlcvBar]:
    if not bars_1m or minutes <= 1:
        return []
    bucket_ms = int(minutes) * 60_000
    out: list[OhlcvBar] = []
    cur_key: int | None = None
    o = h = l = c = 0.0
    vol = qv = 0.0
    sym = bars_1m[0].symbol
    for bar in bars_1m:
        key = (bar.open_ts_ms // bucket_ms) * bucket_ms
        if cur_key is None:
            cur_key = key
            o, h, l, c = bar.open, bar.high, bar.low, bar.close
            vol, qv = bar.volume, bar.quote_volume
            continue
        if key != cur_key:
            out.append(
                OhlcvBar(
                    symbol=sym,
                    timeframe=f"{minutes}m",
                    open_ts_ms=cur_key,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=vol,
                    quote_volume=qv,
                    trade_count=0,
                    closed=True,
                )
            )
            cur_key = key
            o, h, l, c = bar.open, bar.high, bar.low, bar.close
            vol, qv = bar.volume, bar.quote_volume
        else:
            h = max(h, bar.high)
            l = min(l, bar.low)
            c = bar.close
            vol += bar.volume
            qv += bar.quote_volume
    if cur_key is not None:
        out.append(
            OhlcvBar(
                symbol=sym,
                timeframe=f"{minutes}m",
                open_ts_ms=cur_key,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=vol,
                quote_volume=qv,
                trade_count=0,
                closed=True,
            )
        )
    return out


@dataclass
class ScalpDataset:
    X: np.ndarray
    y: np.ndarray
    timestamps_ms: np.ndarray
    symbols: np.ndarray
    feature_names: tuple[str, ...] = FEATURE_NAMES
    returns: np.ndarray | None = field(default=None)

    @property
    def n_samples(self) -> int:
        return int(self.y.shape[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "positive_rate": float(np.mean(self.y)) if self.n_samples else 0.0,
            "feature_names": list(self.feature_names),
        }


def _emit_higher_tf(engine: FeatureEngine, symbol: str, bars_1m: list[OhlcvBar], upto: int) -> None:
    slice_1m = bars_1m[: upto + 1]
    for bar in aggregate_higher_tf(slice_1m, 3):
        engine.on_bar(bar)
    for bar in aggregate_higher_tf(slice_1m, 15):
        engine.on_bar(bar)


def build_dataset_from_bars(
    bars_by_symbol: dict[str, list[OhlcvBar]],
    *,
    label_cfg: ScalpLabelConfig | None = None,
    btc_symbol: str = "BTCUSDT",
    min_warmup_bars: int = 61,
    fear_greed_path: str | Path | None = None,
) -> ScalpDataset:
    cfg = label_cfg or ScalpLabelConfig()
    horizon = int(cfg.horizon_minutes)
    btc_bars = bars_by_symbol.get(btc_symbol.upper(), [])

    xs: list[np.ndarray] = []
    ys: list[int] = []
    ts_list: list[int] = []
    sym_list: list[str] = []

    for symbol, bars_1m in bars_by_symbol.items():
        if len(bars_1m) < min_warmup_bars + horizon + 1:
            continue
        eng = FeatureEngine(btc_symbol=btc_symbol, fear_greed_path=fear_greed_path)
        btc_idx = 0
        last_3m_key = last_15m_key = None

        for i, bar in enumerate(bars_1m):
            while btc_idx < len(btc_bars) and btc_bars[btc_idx].open_ts_ms <= bar.open_ts_ms:
                eng.on_bar(btc_bars[btc_idx])
                btc_idx += 1

            eng.on_bar(bar)
            slice_1m = bars_1m[: i + 1]
            b3 = aggregate_higher_tf(slice_1m, 3)
            if b3:
                k3 = b3[-1].open_ts_ms
                if k3 != last_3m_key:
                    eng.on_bar(b3[-1])
                    last_3m_key = k3
            b15 = aggregate_higher_tf(slice_1m, 15)
            if b15:
                k15 = b15[-1].open_ts_ms
                if k15 != last_15m_key:
                    eng.on_bar(b15[-1])
                    last_15m_key = k15

            if i < min_warmup_bars:
                continue
            j = i + horizon
            if j >= len(bars_1m):
                break
            y = cfg.label_from_prices(bars_1m[i].close, bars_1m[j].close)
            if y is None:
                continue
            vec = eng.compute(symbol, forward_fill=True)
            xs.append(vec)
            ys.append(int(y))
            ts_list.append(int(bar.open_ts_ms))
            sym_list.append(symbol.upper())

    if not xs:
        return ScalpDataset(
            X=np.zeros((0, FEATURE_COUNT)),
            y=np.zeros(0, dtype=np.int8),
            timestamps_ms=np.zeros(0, dtype=np.int64),
            symbols=np.array([], dtype=object),
        )

    return ScalpDataset(
        X=np.vstack(xs),
        y=np.asarray(ys, dtype=np.int8),
        timestamps_ms=np.asarray(ts_list, dtype=np.int64),
        symbols=np.asarray(sym_list, dtype=object),
    )


def load_dataset_from_bars_dir(
    bars_dir: str | Path,
    *,
    symbols: list[str] | None = None,
    label_cfg: ScalpLabelConfig | None = None,
    btc_symbol: str = "BTCUSDT",
    max_bars_per_symbol: int = 0,
) -> ScalpDataset:
    root = Path(bars_dir)
    bars_by_symbol: dict[str, list[OhlcvBar]] = {}

    def _cap(bars: list[OhlcvBar]) -> list[OhlcvBar]:
        # 종목당 최근 N봉만 사용. 데이터셋 빌드가 봉마다 과거 전체를 재집계해
        # O(n^2)이므로(특히 BTC/ETH 95k봉), 상한으로 비용을 묶고 최근성도 확보한다.
        if max_bars_per_symbol and len(bars) > max_bars_per_symbol:
            return bars[-max_bars_per_symbol:]
        return bars

    if symbols:
        wanted = {s.upper() for s in symbols}
    else:
        wanted = None
    for path in sorted(root.glob("*_1m.jsonl")):
        if path.name.startswith("._"):
            continue
        sym = path.name.replace("_1m.jsonl", "").upper()
        if wanted is not None and sym not in wanted:
            continue
        bars = _cap(load_bars_jsonl(path))
        if bars:
            bars_by_symbol[sym] = bars
    if btc_symbol.upper() not in bars_by_symbol:
        btc_path = root / f"{btc_symbol.upper()}_1m.jsonl"
        if btc_path.is_file():
            bars_by_symbol[btc_symbol.upper()] = _cap(load_bars_jsonl(btc_path))
    return build_dataset_from_bars(
        bars_by_symbol,
        label_cfg=label_cfg,
        btc_symbol=btc_symbol,
    )


def iter_bar_paths(bars_dir: Path) -> Iterator[Path]:
    for path in sorted(bars_dir.glob("*_1m.jsonl")):
        if path.name.startswith("._"):
            continue
        yield path
