"""In-memory ML validation: replay features, TimeSeries CV, overfit report, threshold sweep."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from deepsignal.market_data.binance_stream.models import OhlcvBar
from deepsignal.market_data.feature_engine.engine import FeatureEngine
from deepsignal.market_data.feature_engine.fear_greed import default_cache_path
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES
from deepsignal.ml.crypto_scalp_dataset import ScalpDataset, load_bars_jsonl
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
from deepsignal.ml.crypto_scalp_lgbm import _binary_metrics
from deepsignal.ml.crypto_sharpe import sharpe_from_returns

DATA_SOURCE_MISMATCH_WARNING = (
    "피처=Binance, 체결=Upbit — 스프레드 차이 있음. "
    "검증 수익률은 Binance 1m 봉·호가 스냅샷 기준이며 실제 Upbit 체결과 괴리될 수 있습니다."
)

DEFAULT_PROB_GRID = (0.50, 0.52, 0.55, 0.58, 0.60)
DEFAULT_HORIZON_GRID = (3, 5, 10)


@dataclass(frozen=True)
class ValidateMlConfig:
    horizon_minutes: int = 5
    fee_rate: float = 0.0005
    """Per-side fee fraction (0.0005 = 0.05%). Label hurdle = fee_rate * 2."""
    slippage_spread_frac: float = 0.5
    n_splits: int = 5
    gap: int = 10
    buy_threshold: float = 0.55
    min_warmup_bars: int = 61
    btc_symbol: str = "BTCUSDT"
    random_state: int = 42
    lgbm_params: dict[str, Any] = field(
        default_factory=lambda: {
            "objective": "binary",
            "metric": "auc",
            "n_estimators": 300,
            "learning_rate": 0.05,
            "max_depth": 5,
            "num_leaves": 31,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "verbose": -1,
        }
    )

    @property
    def hurdle_fraction(self) -> float:
        return float(self.fee_rate) * 2.0


@dataclass
class FoldValidationRow:
    fold: int
    train_size: int
    val_size: int
    train_auc: float
    val_auc: float
    train_win_rate: float
    val_win_rate: float
    train_ev_pct: float
    val_ev_pct: float
    train_sharpe: float
    val_sharpe: float
    train_trades: int
    val_trades: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThresholdSweepRow:
    horizon_minutes: int
    prob_threshold: float
    n_trades: int
    win_rate: float
    ev_pct: float
    sharpe: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_symbols(raw: str | list[str] | None) -> list[str]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace(" ", ",").split(",") if p.strip()]
    else:
        parts = [str(p).strip().upper() for p in raw if str(p).strip()]
    out: list[str] = []
    for p in parts:
        if p.endswith("USDT"):
            out.append(p)
        elif p.startswith("KRW-"):
            out.append(p.replace("KRW-", "") + "USDT")
        else:
            out.append(f"{p}USDT")
    return out


def filter_bars_last_days(bars: list[OhlcvBar], days: int) -> list[OhlcvBar]:
    if not bars or days <= 0:
        return bars
    last_ts = bars[-1].open_ts_ms
    cutoff = last_ts - int(days) * 86_400_000
    return [b for b in bars if b.open_ts_ms >= cutoff]


def _bar_close_ms(bar: OhlcvBar) -> int:
    return int(bar.open_ts_ms) + 60_000


def _load_ob_index(ob_path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not ob_path.is_file():
        return []
    rows: list[tuple[int, dict[str, Any]]] = []
    for line in ob_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            ts_ms = int(row.get("ts_ms") or int(row.get("ts", 0)) * 1000)
            rows.append((ts_ms, row))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def _latest_ob_at(ob_index: list[tuple[int, dict[str, Any]]], ts_ms: int) -> dict[str, Any] | None:
    out = None
    for t, row in ob_index:
        if t < ts_ms:
            out = row
        else:
            break
    return out


def effective_buy_price(
    bar: OhlcvBar,
    ob_row: dict[str, Any] | None,
    *,
    slippage_spread_frac: float,
) -> float:
    if ob_row and ob_row.get("asks") and ob_row.get("bids"):
        ask = float(ob_row["asks"][0][0])
        bid = float(ob_row["bids"][0][0])
        spread = max(0.0, ask - bid)
        return ask + spread * float(slippage_spread_frac)
    return float(bar.close)


def effective_sell_price(
    bar: OhlcvBar,
    ob_row: dict[str, Any] | None,
    *,
    slippage_spread_frac: float,
) -> float:
    if ob_row and ob_row.get("bids") and ob_row.get("asks"):
        bid = float(ob_row["bids"][0][0])
        ask = float(ob_row["asks"][0][0])
        spread = max(0.0, ask - bid)
        return bid - spread * float(slippage_spread_frac)
    return float(bar.close)


def build_replay_dataset(
    bars_by_symbol: dict[str, list[OlcvBar]],
    *,
    stream_dir: str | Path,
    cfg: ValidateMlConfig,
    fear_greed_path: str | Path | None = None,
) -> tuple[ScalpDataset, np.ndarray]:
    """
  Build X via FeatureEngine.replay_at at each 1m bar close.
  Returns dataset and per-row net return %% (after round-trip fee) for trade metrics.
    """
    stream = Path(stream_dir)
    horizon = int(cfg.horizon_minutes)
    hurdle = cfg.hurdle_fraction
    fg_path = fear_greed_path or default_cache_path(stream.parent)

    xs: list[np.ndarray] = []
    ys: list[int] = []
    ts_list: list[int] = []
    sym_list: list[str] = []
    net_rets_pct: list[float] = []

    for symbol, bars_1m in bars_by_symbol.items():
        if len(bars_1m) < cfg.min_warmup_bars + horizon + 1:
            continue
        ob_index = _load_ob_index(stream / "bars" / f"{symbol.upper()}_ob.jsonl")
        eng = FeatureEngine(btc_symbol=cfg.btc_symbol, fear_greed_path=fg_path)

        for i in range(cfg.min_warmup_bars, len(bars_1m) - horizon):
            bar = bars_1m[i]
            exit_bar = bars_1m[i + horizon]
            ts_ms = _bar_close_ms(bar)
            vec = eng.replay_at(
                symbol,
                ts_ms,
                stream_dir=stream,
                forward_fill=False,
            )
            if vec.shape[0] != FEATURE_COUNT or np.all(np.isnan(vec)):
                continue

            ob_entry = _latest_ob_at(ob_index, ts_ms)
            ob_exit = _latest_ob_at(ob_index, _bar_close_ms(exit_bar))
            entry_px = effective_buy_price(
                bar, ob_entry, slippage_spread_frac=cfg.slippage_spread_frac
            )
            exit_px = effective_sell_price(
                exit_bar, ob_exit, slippage_spread_frac=cfg.slippage_spread_frac
            )
            if entry_px <= 0 or exit_px <= 0:
                continue
            gross = (exit_px / entry_px) - 1.0
            net = gross - 2.0 * float(cfg.fee_rate)
            y = 1 if gross > hurdle else 0

            xs.append(vec)
            ys.append(y)
            ts_list.append(ts_ms)
            sym_list.append(symbol.upper())
            net_rets_pct.append(net * 100.0)

    if not xs:
        empty = ScalpDataset(
            X=np.zeros((0, FEATURE_COUNT)),
            y=np.zeros(0, dtype=np.int8),
            timestamps_ms=np.zeros(0, dtype=np.int64),
            symbols=np.array([], dtype=object),
        )
        return empty, np.zeros(0, dtype=np.float64)

    return (
        ScalpDataset(
            X=np.vstack(xs),
            y=np.asarray(ys, dtype=np.int8),
            timestamps_ms=np.asarray(ts_list, dtype=np.int64),
            symbols=np.asarray(sym_list, dtype=object),
        ),
        np.asarray(net_rets_pct, dtype=np.float64),
    )


def load_bars_for_validation(
    bars_dir: str | Path,
    *,
    symbols: list[str],
    days: int,
    btc_symbol: str = "BTCUSDT",
) -> dict[str, list[OhlcvBar]]:
    root = Path(bars_dir)
    wanted = {s.upper() for s in symbols} if symbols else None
    out: dict[str, list[OhlcvBar]] = {}
    for path in sorted(root.glob("*_1m.jsonl")):
        sym = path.name.replace("_1m.jsonl", "").upper()
        if wanted is not None and sym not in wanted:
            continue
        bars = filter_bars_last_days(load_bars_jsonl(path), days)
        if bars:
            out[sym] = bars
    btc = btc_symbol.upper()
    if btc not in out:
        p = root / f"{btc}_1m.jsonl"
        if p.is_file():
            out[btc] = filter_bars_last_days(load_bars_jsonl(p), days)
    return out


def _trade_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    net_rets_pct: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    mask = y_prob >= float(threshold)
    n = int(np.sum(mask))
    if n < 1:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "ev_pct": 0.0,
            "sharpe": 0.0,
        }
    rets = net_rets_pct[mask]
    wins = float(np.mean(rets > 0)) if n else 0.0
    return {
        "n_trades": n,
        "win_rate": wins,
        "ev_pct": float(np.mean(rets)),
        "sharpe": float(sharpe_from_returns(rets.tolist())),
    }


def _fold_status(train_sharpe: float, val_sharpe: float) -> str:
    if val_sharpe < train_sharpe * 0.5:
        return "⚠️ OVERFIT"
    return "✅"


def run_timeseries_cv(
    dataset: ScalpDataset,
    net_rets_pct: np.ndarray,
    *,
    cfg: ValidateMlConfig,
) -> tuple[list[FoldValidationRow], np.ndarray]:
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError("lightgbm required: pip install lightgbm scikit-learn") from exc
    from sklearn.model_selection import TimeSeriesSplit

    order = np.argsort(dataset.timestamps_ms)
    X = dataset.X[order]
    y = dataset.y[order]
    rets = net_rets_pct[order]

    tscv = TimeSeriesSplit(n_splits=int(cfg.n_splits), gap=int(cfg.gap))
    folds: list[FoldValidationRow] = []
    oof_prob = np.full(len(y), np.nan, dtype=np.float64)

    for fold_i, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X[train_idx], X[val_idx]
        y_tr, y_va = y[train_idx], y[val_idx]
        model = lgb.LGBMClassifier(**cfg.lgbm_params, random_state=cfg.random_state)
        model.fit(
            X_tr,
            y_tr,
            eval_set=[(X_va, y_va)],
            callbacks=[lgb.early_stopping(40, verbose=False)],
        )
        prob_tr = model.predict_proba(X_tr)[:, 1]
        prob_va = model.predict_proba(X_va)[:, 1]
        oof_prob[val_idx] = prob_va

        m_tr = _binary_metrics(y_tr, prob_tr, cfg.buy_threshold)
        m_va = _binary_metrics(y_va, prob_va, cfg.buy_threshold)
        tm_tr = _trade_metrics(y_tr, prob_tr, rets[train_idx], cfg.buy_threshold)
        tm_va = _trade_metrics(y_va, prob_va, rets[val_idx], cfg.buy_threshold)

        folds.append(
            FoldValidationRow(
                fold=fold_i + 1,
                train_size=int(len(train_idx)),
                val_size=int(len(val_idx)),
                train_auc=m_tr["auc"],
                val_auc=m_va["auc"],
                train_win_rate=tm_tr["win_rate"],
                val_win_rate=tm_va["win_rate"],
                train_ev_pct=tm_tr["ev_pct"],
                val_ev_pct=tm_va["ev_pct"],
                train_sharpe=tm_tr["sharpe"],
                val_sharpe=tm_va["sharpe"],
                train_trades=int(tm_tr["n_trades"]),
                val_trades=int(tm_va["n_trades"]),
                status=_fold_status(tm_tr["sharpe"], tm_va["sharpe"]),
            )
        )
    return folds, oof_prob


def write_validation_report_md(
    path: str | Path,
    *,
    folds: list[FoldValidationRow],
    cfg: ValidateMlConfig,
    dataset: ScalpDataset,
    symbols: list[str],
    days: int,
    extra_warnings: list[str] | None = None,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# CRYPTO ML Validation Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Configuration",
        "",
        f"- Symbols: {', '.join(symbols) if symbols else '(all in bars dir)'}",
        f"- Days: {days}",
        f"- Horizon: {cfg.horizon_minutes}m",
        f"- Fee (per side): {cfg.fee_rate:.4%}",
        f"- Label hurdle (round-trip): {cfg.hurdle_fraction:.4%}",
        f"- Buy threshold P: {cfg.buy_threshold}",
        f"- TimeSeriesSplit: n_splits={cfg.n_splits}, gap={cfg.gap}",
        f"- Slippage: entry ≈ ask + spread×{cfg.slippage_spread_frac}",
        f"- Samples: {dataset.n_samples}, positive rate: {float(np.mean(dataset.y)):.3f}",
        "",
        "## Data source",
        "",
        f"> ⚠️ {DATA_SOURCE_MISMATCH_WARNING}",
        "",
    ]
    for w in extra_warnings or []:
        lines.append(f"> {w}")
        lines.append("")

    lines.extend(
        [
            "## Fold results",
            "",
            "| Fold | Train AUC | Val AUC | Train Sharpe | Val Sharpe | Train EV% | Val EV% | Val trades | Status |",
            "|------|-----------|---------|--------------|------------|-----------|---------|------------|--------|",
        ]
    )
    for f in folds:
        lines.append(
            f"| {f.fold} | {f.train_auc:.2f} | {f.val_auc:.2f} | "
            f"{f.train_sharpe:.1f} | {f.val_sharpe:.1f} | "
            f"{f.train_ev_pct:.3f} | {f.val_ev_pct:.3f} | {f.val_trades} | {f.status} |"
        )

    overfit = [f for f in folds if "OVERFIT" in f.status]
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Folds with OVERFIT warning (val_sharpe < 0.5× train): **{len(overfit)}** / {len(folds)}",
            f"- Mean val AUC: **{np.mean([f.val_auc for f in folds]):.3f}**",
            f"- Mean val Sharpe @ P≥{cfg.buy_threshold}: **{np.mean([f.val_sharpe for f in folds]):.2f}**",
            "",
        ]
    )
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run_threshold_sweep(
    bars_by_symbol: dict[str, list[OhlcvBar]],
    *,
    stream_dir: str | Path,
    fee_rate: float,
    prob_grid: tuple[float, ...] = DEFAULT_PROB_GRID,
    horizon_grid: tuple[int, ...] = DEFAULT_HORIZON_GRID,
    n_splits: int = 5,
    gap: int = 10,
) -> list[ThresholdSweepRow]:
    rows: list[ThresholdSweepRow] = []
    stream = Path(stream_dir)

    for horizon in horizon_grid:
        vcfg = ValidateMlConfig(
            horizon_minutes=int(horizon),
            fee_rate=fee_rate,
            n_splits=n_splits,
            gap=gap,
            buy_threshold=0.55,
        )
        data, net_rets = build_replay_dataset(bars_by_symbol, stream_dir=stream, cfg=vcfg)
        if data.n_samples < 100:
            continue
        _, oof_prob = run_timeseries_cv(data, net_rets, cfg=vcfg)
        valid = ~np.isnan(oof_prob)
        if int(np.sum(valid)) < 50:
            continue
        y = data.y[valid]
        prob = oof_prob[valid]
        rets = net_rets[valid]

        for p in prob_grid:
            tm = _trade_metrics(y, prob, rets, p)
            rows.append(
                ThresholdSweepRow(
                    horizon_minutes=int(horizon),
                    prob_threshold=float(p),
                    n_trades=int(tm["n_trades"]),
                    win_rate=float(tm["win_rate"]),
                    ev_pct=float(tm["ev_pct"]),
                    sharpe=float(tm["sharpe"]),
                )
            )
    return rows


def write_threshold_report_md(
    path: str | Path,
    *,
    rows: list[ThresholdSweepRow],
    fee_rate: float,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    best = max(rows, key=lambda r: r.sharpe) if rows else None

    lines = [
        "# CRYPTO ML Threshold Sweep",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"> ⚠️ {DATA_SOURCE_MISMATCH_WARNING}",
        "",
        f"- Fee (per side): {fee_rate:.4%}",
        f"- Grid P: {list(DEFAULT_PROB_GRID)}",
        f"- Grid N (minutes): {list(DEFAULT_HORIZON_GRID)}",
        "",
        "## Grid results",
        "",
        "| N (min) | P | Trades | Win rate | EV % | Sharpe |",
        "|---------|---|--------|----------|------|--------|",
    ]
    for r in sorted(rows, key=lambda x: (-x.sharpe, -x.n_trades)):
        lines.append(
            f"| {r.horizon_minutes} | {r.prob_threshold:.2f} | {r.n_trades} | "
            f"{r.win_rate:.1%} | {r.ev_pct:.3f} | {r.sharpe:.2f} |"
        )

    lines.extend(["", "## Recommendation", ""])
    if best and best.n_trades >= 5:
        lines.append(
            f"- **권장**: `P={best.prob_threshold:.2f}`, `N={best.horizon_minutes}m` "
            f"(OOF Sharpe **{best.sharpe:.2f}**, trades={best.n_trades}, EV={best.ev_pct:.3f}%)"
        )
        lines.append(
            f"- Env: `CRYPTO_ML_BUY_THRESHOLD={best.prob_threshold:.2f}`, "
            f"train with `--horizon {best.horizon_minutes}`"
        )
    else:
        lines.append("- 데이터 부족 또는 유효 거래 없음 — `binance-stream` 기간을 늘린 뒤 재실행하세요.")

    lines.append("")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run_full_validation(
    *,
    bars_dir: str | Path,
    stream_dir: str | Path,
    output_dir: str | Path,
    symbols: list[str],
    days: int,
    cfg: ValidateMlConfig,
    run_sweep: bool = True,
) -> dict[str, Any]:
    bars_by = load_bars_for_validation(
        bars_dir, symbols=symbols, days=days, btc_symbol=cfg.btc_symbol
    )
    if not bars_by:
        raise FileNotFoundError(f"No 1m bars under {bars_dir} for symbols={symbols}")

    data, net_rets = build_replay_dataset(bars_by, stream_dir=stream_dir, cfg=cfg)
    if data.n_samples < 100:
        raise ValueError(
            f"Too few samples ({data.n_samples}). Need more bars (days={days}, symbols={symbols})."
        )

    folds, _oof = run_timeseries_cv(data, net_rets, cfg=cfg)
    out = Path(output_dir)
    report_path = write_validation_report_md(
        out / "CRYPTO_ML_VALIDATION_REPORT.md",
        folds=folds,
        cfg=cfg,
        dataset=data,
        symbols=list(bars_by.keys()),
        days=days,
    )

    sweep_rows: list[ThresholdSweepRow] = []
    threshold_path = None
    if run_sweep:
        sweep_rows = run_threshold_sweep(
            bars_by,
            stream_dir=stream_dir,
            fee_rate=cfg.fee_rate,
        )
        threshold_path = write_threshold_report_md(
            out / "CRYPTO_ML_THRESHOLD_REPORT.md",
            rows=sweep_rows,
            fee_rate=cfg.fee_rate,
        )

    json_path = out / "crypto_ml_validation_latest.json"
    payload = {
        "config": {
            "horizon_minutes": cfg.horizon_minutes,
            "fee_rate": cfg.fee_rate,
            "hurdle_fraction": cfg.hurdle_fraction,
            "buy_threshold": cfg.buy_threshold,
            "n_splits": cfg.n_splits,
            "gap": cfg.gap,
            "days": days,
            "symbols": list(bars_by.keys()),
        },
        "dataset": data.to_dict(),
        "folds": [f.to_dict() for f in folds],
        "sweep": [r.to_dict() for r in sweep_rows],
        "reports": {
            "validation_md": report_path.as_posix(),
            "threshold_md": threshold_path.as_posix() if threshold_path else None,
        },
        "data_source_warning": DATA_SOURCE_MISMATCH_WARNING,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return payload
