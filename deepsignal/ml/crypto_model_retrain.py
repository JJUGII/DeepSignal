"""Nightly LightGBM (+ optional seq) retrain with trade feedback and deploy gates."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_trades import count_closed_trades, init_crypto_trades_db
from deepsignal.live_trading.time_utils import now_kst_iso
from deepsignal.ml.crypto_retrain_history import RetrainHistoryEntry, append_retrain_history
from deepsignal.ml.crypto_scalp_dataset import load_dataset_from_bars_dir
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
from deepsignal.ml.crypto_scalp_lgbm import LgbmTrainConfig, train_lgbm_classifier
from deepsignal.ml.crypto_sharpe import sharpe_from_outcomes_db
from deepsignal.ml.crypto_trades_dataset import load_dataset_from_crypto_trades


@dataclass
class RetrainResult:
    deployed: bool
    reason: str
    candidate_auc: float
    baseline_auc: float
    candidate_sharpe: float
    baseline_sharpe: float
    val_sharpe: float = 0.0
    train_sharpe: float = 0.0
    n_trades_used: int = 0
    model_version: str = ""
    model_path: str = ""
    report_path: str = ""
    meta_path: str = ""
    seq_deployed: bool = False
    warm_start: bool = False
    also_seq: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrainOptions:
    output_dir: str | Path = "outputs"
    bars_dir: str | Path | None = None
    horizon_minutes: int = 5
    cost_pct: float = 0.2
    min_samples: int = 200
    trade_lookback_days: int = 14
    min_trades_deploy: int = 30
    min_val_auc: float = 0.52
    sharpe_ratio_min: float = 0.5
    also_seq: bool = False
    warm_start: bool = True
    full_retrain: bool = False
    dry_run: bool = False
    seq_kind: str = "lstm"
    bars_days_seq: int = 30
    notify_telegram: bool = True


def _mean_fold_auc(report: Any) -> float:
    folds = getattr(report, "folds", None) or []
    if not folds:
        return 0.0
    aucs = [float(f.get("auc", 0) if isinstance(f, dict) else getattr(f, "auc", 0)) for f in folds]
    return sum(aucs) / len(aucs) if aucs else 0.0


def _mean_fold_sharpe(report: Any, key: str) -> float:
    folds = getattr(report, "folds", None) or []
    if not folds:
        return 0.0
    vals = [
        float(f.get(key, 0) if isinstance(f, dict) else getattr(f, key, 0))
        for f in folds
    ]
    return sum(vals) / len(vals) if vals else 0.0


def load_active_meta(model_dir: Path) -> dict[str, Any]:
    path = model_dir / "crypto_scalp_lgbm_active.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _next_model_version(model_dir: Path) -> str:
    meta = load_active_meta(model_dir)
    raw = str(meta.get("model_version") or "v0")
    try:
        n = int(raw.lstrip("vV"))
    except ValueError:
        n = 0
    return f"v{n + 1}"


def _notify_retrain_failed(output_dir: Path, message: str) -> None:
    try:
        from deepsignal.crypto_trading.crypto_telegram_flow import (
            load_crypto_telegram_config_from_env,
            telegram_send_plain,
        )

        tg = load_crypto_telegram_config_from_env(output_dir=str(output_dir))
        if tg.bot_token:
            telegram_send_plain(
                tg,
                f"⚠️ [DeepSignal] 재학습 실패 — 기존 모델 유지\n{message}",
            )
    except Exception:
        pass


def _build_training_dataset(
    opts: RetrainOptions,
    *,
    trades_db: Path,
    bars: Path,
    label_cfg: ScalpLabelConfig,
) -> tuple[Any, int, str]:
    """Return (ScalpDataset, n_trades_used, source_tag)."""
    trade_ds = load_dataset_from_crypto_trades(
        trades_db,
        lookback_days=opts.trade_lookback_days,
        label_cfg=label_cfg,
    )
    n_trades = count_closed_trades(trades_db, lookback_days=opts.trade_lookback_days)

    if trade_ds is not None and trade_ds.n_samples >= int(opts.min_trades_deploy):
        return trade_ds, n_trades, "crypto_trades"

    if trade_ds is not None and trade_ds.n_samples >= 10:
        bar_ds = load_dataset_from_bars_dir(bars, label_cfg=label_cfg)
        if bar_ds.n_samples > 0:
            import numpy as np

            X = np.vstack([trade_ds.X, bar_ds.X])
            y = np.concatenate([trade_ds.y, bar_ds.y])
            ts = np.concatenate([trade_ds.timestamps_ms, bar_ds.timestamps_ms])
            sym = np.concatenate([trade_ds.symbols, bar_ds.symbols])
            rets = None
            if trade_ds.returns is not None and bar_ds.returns is not None:
                rets = np.concatenate([trade_ds.returns, bar_ds.returns])
            elif trade_ds.returns is not None:
                pad = np.zeros(bar_ds.n_samples, dtype=np.float64)
                rets = np.concatenate([trade_ds.returns, pad])
            from deepsignal.ml.crypto_scalp_dataset import ScalpDataset

            merged = ScalpDataset(
                X=X,
                y=y,
                timestamps_ms=ts,
                symbols=sym,
                feature_names=trade_ds.feature_names,
                returns=rets,
            )
            return merged, n_trades, "trades+bars"

    bar_ds = load_dataset_from_bars_dir(bars, label_cfg=label_cfg)
    return bar_ds, n_trades, "bars"


def _run_seq_retrain(
    *,
    bars: Path,
    model_dir: Path,
    horizon_minutes: int,
    cost_pct: float,
    seq_kind: str,
) -> tuple[bool, str]:
    try:
        from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
        from deepsignal.ml.crypto_scalp_seq_models import (
            SeqTrainConfig,
            load_sequence_dataset_from_bars_dir,
            train_sequence_classifier,
            torch_available,
        )

        if not torch_available():
            return False, "pytorch_unavailable"
        ds = load_sequence_dataset_from_bars_dir(
            bars,
            seq_len=30,
            label_cfg=ScalpLabelConfig(horizon_minutes=horizon_minutes, cost_pct=cost_pct),
        )
        if ds.n_samples < 300:
            return False, f"seq_insufficient_samples:{ds.n_samples}"
        prod = model_dir / f"crypto_scalp_{seq_kind}_{horizon_minutes}m.pt"
        backup = model_dir / f"crypto_scalp_{seq_kind}_{horizon_minutes}m_backup.pt"
        if prod.is_file():
            shutil.copy2(prod, backup)
        cfg = SeqTrainConfig(model_kind=seq_kind, horizon_minutes=horizon_minutes, cost_pct=cost_pct)
        _model, report = train_sequence_classifier(ds, train_cfg=cfg, model_dir=model_dir)
        mean_auc = float(getattr(report, "mean_val_auc", 0) or _mean_fold_auc(report))
        if mean_auc < 0.50:
            if backup.is_file():
                shutil.copy2(backup, prod)
            return False, f"seq_auc_low:{mean_auc:.3f}"
        return True, f"seq_deployed auc={mean_auc:.3f}"
    except Exception as exc:
        return False, f"seq_error:{exc}"


def run_crypto_lgbm_retrain(
    *,
    output_dir: str | Path = "outputs",
    bars_dir: str | Path | None = None,
    horizon_minutes: int = 5,
    cost_pct: float = 0.2,
    min_samples: int = 200,
    min_auc_improvement: float = 0.0,
    min_sharpe_improvement: float = 0.0,
    sharpe_lookback_days: int = 14,
    require_sharpe: bool = True,
    dry_run: bool = False,
    also_seq: bool = False,
    warm_start: bool = True,
    full_retrain: bool = False,
    trade_lookback_days: int = 14,
    min_trades_deploy: int = 30,
    min_val_auc: float = 0.52,
    sharpe_ratio_min: float = 0.5,
    notify_telegram: bool = True,
    seq_kind: str = "lstm",
) -> RetrainResult:
    opts = RetrainOptions(
        output_dir=output_dir,
        bars_dir=bars_dir,
        horizon_minutes=horizon_minutes,
        cost_pct=cost_pct,
        min_samples=min_samples,
        trade_lookback_days=trade_lookback_days,
        min_trades_deploy=min_trades_deploy,
        min_val_auc=min_val_auc,
        sharpe_ratio_min=sharpe_ratio_min,
        also_seq=also_seq,
        warm_start=warm_start and not full_retrain,
        full_retrain=full_retrain,
        dry_run=dry_run,
        seq_kind=seq_kind,
        notify_telegram=notify_telegram,
    )
    return run_crypto_lgbm_retrain_with_options(opts)


def run_crypto_lgbm_retrain_with_options(opts: RetrainOptions) -> RetrainResult:
    out = Path(opts.output_dir)
    model_dir = out / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    bars = Path(opts.bars_dir) if opts.bars_dir else out / "binance_stream" / "bars"
    trades_db = init_crypto_trades_db(out)
    label_cfg = ScalpLabelConfig(horizon_minutes=opts.horizon_minutes, cost_pct=opts.cost_pct)

    prod_name = f"crypto_scalp_lgbm_{opts.horizon_minutes}m.txt"
    prod_path = model_dir / prod_name
    backup_path = model_dir / f"crypto_scalp_lgbm_{opts.horizon_minutes}m_backup.txt"
    alias_path = model_dir / "lgbm_model.txt"

    meta = load_active_meta(model_dir)
    version = _next_model_version(model_dir)

    try:
        ds, n_trades, source = _build_training_dataset(opts, trades_db=trades_db, bars=bars, label_cfg=label_cfg)
    except Exception as exc:
        reason = f"dataset_error:{exc}"
        if opts.notify_telegram:
            _notify_retrain_failed(out, reason)
        return RetrainResult(
            deployed=False,
            reason=reason,
            candidate_auc=0.0,
            baseline_auc=0.0,
            candidate_sharpe=0.0,
            baseline_sharpe=0.0,
            n_trades_used=0,
            model_version=version,
            meta_path=str(model_dir / "crypto_scalp_lgbm_active.json"),
        )

    if ds.n_samples < opts.min_samples:
        reason = f"insufficient_samples:{ds.n_samples}<{opts.min_samples}"
        if opts.notify_telegram:
            _notify_retrain_failed(out, reason)
        return RetrainResult(
            deployed=False,
            reason=reason,
            candidate_auc=0.0,
            baseline_auc=0.0,
            candidate_sharpe=0.0,
            baseline_sharpe=0.0,
            n_trades_used=n_trades,
            model_version=version,
            meta_path=str(model_dir / "crypto_scalp_lgbm_active.json"),
        )

    if opts.dry_run:
        return RetrainResult(
            deployed=False,
            reason="dry_run",
            candidate_auc=0.0,
            baseline_auc=0.0,
            candidate_sharpe=0.0,
            baseline_sharpe=0.0,
            n_trades_used=n_trades,
            model_version=version,
            meta_path=str(model_dir / "crypto_scalp_lgbm_active.json"),
        )

    if prod_path.is_file():
        shutil.copy2(prod_path, backup_path)

    init_path: str | None = None
    if opts.warm_start and prod_path.is_file():
        init_path = str(prod_path)

    train_cfg = LgbmTrainConfig(
        horizon_minutes=opts.horizon_minutes,
        cost_pct=opts.cost_pct,
        min_train_samples=opts.min_samples,
    )
    _model, report = train_lgbm_classifier(
        ds,
        train_cfg=train_cfg,
        model_dir=model_dir,
        init_model_path=init_path,
    )

    candidate_auc = _mean_fold_auc(report)
    train_sharpe = _mean_fold_sharpe(report, "train_sharpe")
    val_sharpe = _mean_fold_sharpe(report, "val_sharpe")

    baseline_auc = float(meta.get("mean_val_auc") or meta.get("baseline_auc") or 0.0)
    candidate_sharpe, _ = sharpe_from_outcomes_db(out, lookback_days=int(opts.trade_lookback_days))
    baseline_sharpe = float(meta.get("realized_sharpe") or meta.get("baseline_sharpe") or 0.0)

    auc_ok = candidate_auc >= float(opts.min_val_auc)
    sharpe_ok = True
    if train_sharpe > 1e-6:
        sharpe_ok = val_sharpe >= train_sharpe * float(opts.sharpe_ratio_min)
    elif val_sharpe < 0:
        sharpe_ok = False
    trades_ok = n_trades >= int(opts.min_trades_deploy)

    deploy = auc_ok and sharpe_ok and trades_ok

    report_dict = report.to_dict()
    report_dict.update(
        {
            "mean_val_auc": candidate_auc,
            "mean_train_sharpe": train_sharpe,
            "mean_val_sharpe": val_sharpe,
            "n_trades_used": n_trades,
            "data_source": source,
            "warm_start": bool(init_path),
            "deployed": deploy,
            "deploy_gates": {
                "auc_ok": auc_ok,
                "sharpe_ok": sharpe_ok,
                "trades_ok": trades_ok,
            },
        }
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    audit_path = model_dir / f"crypto_retrain_audit_{ts}.json"
    audit_path.write_text(json.dumps(report_dict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    active_path = model_dir / "crypto_scalp_lgbm_active.json"
    seq_deployed = False
    seq_reason = ""

    if deploy:
        shutil.copy2(prod_path, alias_path)
        pkl_path = model_dir / "lgbm_model.pkl"
        try:
            import pickle

            with pkl_path.open("wb") as fh:
                pickle.dump(_model, fh)
        except Exception:
            pass
        reason = (
            f"deployed: auc={candidate_auc:.4f} val_sharpe={val_sharpe:.2f} "
            f"train_sharpe={train_sharpe:.2f} trades={n_trades} source={source}"
        )
        active_payload = {
            "model_path": prod_path.as_posix(),
            "horizon_minutes": opts.horizon_minutes,
            "mean_val_auc": candidate_auc,
            "mean_val_sharpe": val_sharpe,
            "mean_train_sharpe": train_sharpe,
            "realized_sharpe": candidate_sharpe,
            "model_version": version,
            "deployed_at": now_kst_iso(),
            "report": audit_path.as_posix(),
            "n_trades_used": n_trades,
        }
        active_path.write_text(json.dumps(active_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if opts.also_seq:
            seq_deployed, seq_reason = _run_seq_retrain(
                bars=bars,
                model_dir=model_dir,
                horizon_minutes=opts.horizon_minutes,
                cost_pct=opts.cost_pct,
                seq_kind=opts.seq_kind,
            )
            if not seq_deployed:
                reason += f"; seq_skipped:{seq_reason}"
    else:
        reason = (
            f"rollback: auc_ok={auc_ok} sharpe_ok={sharpe_ok} trades_ok={trades_ok} "
            f"auc={candidate_auc:.4f} val_sharpe={val_sharpe:.2f} train_sharpe={train_sharpe:.2f} "
            f"trades={n_trades}/{opts.min_trades_deploy}"
        )
        if backup_path.is_file():
            shutil.copy2(backup_path, prod_path)
        if opts.notify_telegram:
            _notify_retrain_failed(out, reason)

    append_retrain_history(
        out,
        RetrainHistoryEntry(
            date=now_kst_iso(),
            val_auc=candidate_auc,
            val_sharpe=val_sharpe,
            train_sharpe=train_sharpe,
            deployed=deploy,
            n_trades_used=n_trades,
            model_version=version if deploy else str(meta.get("model_version") or version),
            reason=reason,
            also_seq=opts.also_seq,
            warm_start=bool(init_path),
        ),
    )

    return RetrainResult(
        deployed=deploy,
        reason=reason,
        candidate_auc=candidate_auc,
        baseline_auc=baseline_auc,
        candidate_sharpe=candidate_sharpe,
        baseline_sharpe=baseline_sharpe,
        val_sharpe=val_sharpe,
        train_sharpe=train_sharpe,
        n_trades_used=n_trades,
        model_version=version if deploy else str(meta.get("model_version") or version),
        model_path=prod_path.as_posix() if prod_path.is_file() else "",
        report_path=audit_path.as_posix(),
        meta_path=active_path.as_posix(),
        seq_deployed=seq_deployed,
        warm_start=bool(init_path),
        also_seq=opts.also_seq,
    )
