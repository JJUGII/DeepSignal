"""LightGBM binary classifier — P(win) after N minutes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from deepsignal.market_data.feature_engine.spec import FEATURE_NAMES
from deepsignal.ml.crypto_scalp_dataset import ScalpDataset
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig


@dataclass
class LgbmTrainConfig:
    horizon_minutes: int = 5
    cost_pct: float = 0.2
    n_splits: int = 5
    buy_threshold: float = 0.55
    random_state: int = 42
    min_train_samples: int = 200
    lgbm_params: dict[str, Any] = field(
        default_factory=lambda: {
            "objective": "binary",
            "metric": "auc",
            "n_estimators": 400,
            "learning_rate": 0.05,
            "max_depth": 6,
            "num_leaves": 31,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 0.1,
            "min_child_samples": 40,
            "verbose": -1,
        }
    )


@dataclass
class FoldMetrics:
    fold: int
    train_size: int
    val_size: int
    accuracy: float
    precision: float
    recall: float
    auc: float
    positive_rate_val: float
    train_sharpe: float = 0.0
    val_sharpe: float = 0.0


@dataclass
class LgbmTrainReport:
    config: dict[str, Any]
    dataset: dict[str, Any]
    folds: list[dict[str, Any]]
    feature_importance: list[dict[str, Any]]
    model_path: str
    buy_threshold: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sort_by_time(dataset: ScalpDataset) -> ScalpDataset:
    order = np.argsort(dataset.timestamps_ms)
    return ScalpDataset(
        X=dataset.X[order],
        y=dataset.y[order],
        timestamps_ms=dataset.timestamps_ms[order],
        symbols=dataset.symbols[order],
        feature_names=dataset.feature_names,
    )


def _binary_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

    y_pred = (y_prob >= threshold).astype(np.int8)
    auc = 0.5
    if len(np.unique(y_true)) > 1:
        auc = float(roc_auc_score(y_true, y_prob))
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "auc": auc,
        "positive_rate_val": float(np.mean(y_true)),
    }


def _resolve_init_model(
    init_model_path: str | Path | None,
    n_features: int,
) -> str | None:
    if not init_model_path:
        return None
    path = Path(init_model_path)
    if not path.is_file():
        return None
    try:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=str(path))
        if int(booster.num_feature()) != int(n_features):
            return None
        return str(path)
    except Exception:
        return None


def _fold_strategy_sharpe(
    returns: np.ndarray | None,
    y_prob: np.ndarray,
    threshold: float,
) -> float:
    if returns is None or len(returns) < 3:
        return 0.0
    from deepsignal.ml.crypto_sharpe import sharpe_from_fraction_returns

    mask = y_prob >= float(threshold)
    if int(np.sum(mask)) < 3:
        return 0.0
    return float(sharpe_from_fraction_returns(returns[mask]))


def train_lgbm_classifier(
    dataset: ScalpDataset,
    *,
    train_cfg: LgbmTrainConfig | None = None,
    model_dir: str | Path = "outputs/models",
    init_model_path: str | Path | None = None,
) -> tuple[Any, LgbmTrainReport]:
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError("lightgbm is required: pip install lightgbm scikit-learn") from exc
    from sklearn.model_selection import TimeSeriesSplit

    cfg = train_cfg or LgbmTrainConfig()
    data = _sort_by_time(dataset)
    if data.n_samples < cfg.min_train_samples:
        raise ValueError(
            f"need at least {cfg.min_train_samples} samples, got {data.n_samples}"
        )

    tscv = TimeSeriesSplit(n_splits=int(cfg.n_splits))
    fold_rows: list[FoldMetrics] = []
    importances = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    init_model = _resolve_init_model(init_model_path, data.X.shape[1])
    rets = data.returns

    for fold_i, (train_idx, val_idx) in enumerate(tscv.split(data.X)):
        X_tr, X_va = data.X[train_idx], data.X[val_idx]
        y_tr, y_va = data.y[train_idx], data.y[val_idx]
        model = lgb.LGBMClassifier(**cfg.lgbm_params, random_state=cfg.random_state)
        fit_kw: dict[str, Any] = {
            "eval_set": [(X_va, y_va)],
            "callbacks": [lgb.early_stopping(50, verbose=False)],
        }
        if init_model:
            fit_kw["init_model"] = init_model
        model.fit(X_tr, y_tr, **fit_kw)
        prob_va = model.predict_proba(X_va)[:, 1]
        prob_tr = model.predict_proba(X_tr)[:, 1]
        m = _binary_metrics(y_va, prob_va, cfg.buy_threshold)
        tr_sh = _fold_strategy_sharpe(
            rets[train_idx] if rets is not None else None, prob_tr, cfg.buy_threshold
        )
        va_sh = _fold_strategy_sharpe(
            rets[val_idx] if rets is not None else None, prob_va, cfg.buy_threshold
        )
        fold_rows.append(
            FoldMetrics(
                fold=fold_i,
                train_size=int(len(train_idx)),
                val_size=int(len(val_idx)),
                train_sharpe=tr_sh,
                val_sharpe=va_sh,
                **m,
            )
        )
        importances += model.feature_importances_

    final = lgb.LGBMClassifier(**cfg.lgbm_params, random_state=cfg.random_state)
    final_kw: dict[str, Any] = {}
    if init_model:
        final_kw["init_model"] = init_model
    final.fit(data.X, data.y, **final_kw)

    imp_avg = importances / max(1, len(fold_rows))
    imp_list = [
        {"feature": name, "importance": float(imp_avg[i])}
        for i, name in enumerate(FEATURE_NAMES)
    ]
    imp_list.sort(key=lambda x: x["importance"], reverse=True)

    out_dir = Path(model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"crypto_scalp_lgbm_{cfg.horizon_minutes}m.txt"
    final.booster_.save_model(str(model_path))

    meta_path = out_dir / f"crypto_scalp_lgbm_{cfg.horizon_minutes}m_meta.json"
    label_cfg = ScalpLabelConfig(horizon_minutes=cfg.horizon_minutes, cost_pct=cfg.cost_pct)
    report = LgbmTrainReport(
        config={
            "horizon_minutes": cfg.horizon_minutes,
            "cost_pct": cfg.cost_pct,
            "hurdle_fraction": label_cfg.hurdle_fraction,
            "n_splits": cfg.n_splits,
            "buy_threshold": cfg.buy_threshold,
            "lgbm_params": cfg.lgbm_params,
        },
        dataset=data.to_dict(),
        folds=[asdict(f) for f in fold_rows],
        feature_importance=imp_list,
        model_path=model_path.as_posix(),
        buy_threshold=cfg.buy_threshold,
    )
    meta_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return final, report


def load_lgbm_model(model_path: str | Path) -> Any:
    import lightgbm as lgb

    booster = lgb.Booster(model_file=str(model_path))
    return booster


def predict_proba(model: Any, X: np.ndarray) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        return model.predict_proba(X)[:, 1]
    return model.predict(X)
