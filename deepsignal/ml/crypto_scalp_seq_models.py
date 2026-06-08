"""PyTorch LSTM / Transformer binary classifiers for scalp P(win)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES
from deepsignal.ml.crypto_scalp_sequence_dataset import SequenceDataset

ModelKind = Literal["lstm", "transformer"]


def torch_available() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class SeqTrainConfig:
    model_kind: ModelKind = "lstm"
    seq_len: int = 30
    horizon_minutes: int = 5
    cost_pct: float = 0.2
    n_splits: int = 5
    buy_threshold: float = 0.55
    min_train_samples: int = 300
    epochs: int = 40
    batch_size: int = 64
    learning_rate: float = 1e-3
    dropout: float = 0.3
    early_stop_patience: int = 8
    random_state: int = 42


@dataclass
class SeqTrainReport:
    model_kind: str
    config: dict[str, Any]
    dataset: dict[str, Any]
    folds: list[dict[str, Any]]
    model_path: str
    buy_threshold: float
    mean_val_auc: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_model(kind: ModelKind, n_features: int, *, dropout: float) -> Any:
    import torch
    import torch.nn as nn

    class LstmHead(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lstm = nn.LSTM(n_features, 64, batch_first=True, dropout=dropout if dropout > 0 else 0.0)
            self.drop = nn.Dropout(dropout)
            self.fc = nn.Linear(64, 1)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            out, _ = self.lstm(x)
            h = self.drop(out[:, -1, :])
            return torch.sigmoid(self.fc(h)).squeeze(-1)

    class TransformerHead(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            d_model = 64
            self.proj = nn.Linear(n_features, d_model)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=4,
                dim_feedforward=128,
                dropout=dropout,
                batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=2)
            self.drop = nn.Dropout(dropout)
            self.fc = nn.Linear(d_model, 1)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            h = self.proj(x)
            h = self.encoder(h)
            h = self.drop(h[:, -1, :])
            return torch.sigmoid(self.fc(h)).squeeze(-1)

    if kind == "transformer":
        return TransformerHead()
    return LstmHead()


def _train_one_fold(
    X_tr: np.ndarray,
    y_tr: np.ndarray,
    X_va: np.ndarray,
    y_va: np.ndarray,
    *,
    kind: ModelKind,
    cfg: SeqTrainConfig,
) -> tuple[Any, dict[str, float]]:
    import torch
    import torch.nn as nn
    from sklearn.metrics import roc_auc_score

    device = torch.device("cpu")
    model = _build_model(kind, X_tr.shape[-1], dropout=cfg.dropout).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.learning_rate)
    loss_fn = nn.BCELoss()

    X_tr_t = torch.tensor(X_tr, dtype=torch.float32)
    y_tr_t = torch.tensor(y_tr, dtype=torch.float32)
    X_va_t = torch.tensor(X_va, dtype=torch.float32)
    y_va_t = torch.tensor(y_va, dtype=torch.float32)

    best_auc = 0.0
    best_state: dict[str, Any] | None = None
    patience = 0

    for _epoch in range(int(cfg.epochs)):
        model.train()
        perm = torch.randperm(len(X_tr_t))
        for start in range(0, len(X_tr_t), int(cfg.batch_size)):
            idx = perm[start : start + int(cfg.batch_size)]
            xb = X_tr_t[idx].to(device)
            yb = y_tr_t[idx].to(device)
            opt.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            opt.step()

        model.eval()
        with torch.no_grad():
            prob = model(X_va_t.to(device)).cpu().numpy()
        auc = 0.5
        if len(np.unique(y_va)) > 1:
            auc = float(roc_auc_score(y_va, prob))
        if auc >= best_auc:
            best_auc = auc
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= int(cfg.early_stop_patience):
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, {"auc": best_auc}


def train_sequence_classifier(
    dataset: SequenceDataset,
    *,
    train_cfg: SeqTrainConfig | None = None,
    model_dir: str | Path = "outputs/models",
) -> tuple[Any, SeqTrainReport]:
    if not torch_available():
        raise RuntimeError("PyTorch required: pip install torch")

    import torch
    from sklearn.model_selection import TimeSeriesSplit

    cfg = train_cfg or SeqTrainConfig()
    if dataset.n_samples < cfg.min_train_samples:
        raise ValueError(f"need {cfg.min_train_samples}+ samples, got {dataset.n_samples}")

    order = np.argsort(dataset.timestamps_ms)
    X = dataset.X[order]
    y = dataset.y[order]

    tscv = TimeSeriesSplit(n_splits=int(cfg.n_splits))
    fold_rows: list[dict[str, Any]] = []
    importances_auc: list[float] = []

    for fold_i, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        model, metrics = _train_one_fold(
            X[tr_idx],
            y[tr_idx],
            X[va_idx],
            y[va_idx],
            kind=cfg.model_kind,
            cfg=cfg,
        )
        fold_rows.append(
            {
                "fold": fold_i,
                "train_size": int(len(tr_idx)),
                "val_size": int(len(va_idx)),
                "auc": metrics["auc"],
            }
        )
        importances_auc.append(metrics["auc"])

    final_model, _ = _train_one_fold(X, y, X[-max(50, len(X) // 10) :], y[-max(50, len(y) // 10) :], kind=cfg.model_kind, cfg=cfg)

    out_dir = Path(model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"crypto_scalp_{cfg.model_kind}_{cfg.horizon_minutes}m.pt"
    torch.save(
        {
            "state_dict": final_model.state_dict(),
            "model_kind": cfg.model_kind,
            "n_features": int(X.shape[-1]),
            "seq_len": int(dataset.seq_len),
            "feature_names": list(FEATURE_NAMES),
            "config": asdict(cfg),
        },
        model_path,
    )

    mean_auc = float(np.mean(importances_auc)) if importances_auc else 0.0
    meta_path = out_dir / f"crypto_scalp_{cfg.model_kind}_{cfg.horizon_minutes}m_meta.json"
    report = SeqTrainReport(
        model_kind=cfg.model_kind,
        config=asdict(cfg),
        dataset=dataset.to_dict(),
        folds=fold_rows,
        model_path=model_path.as_posix(),
        buy_threshold=cfg.buy_threshold,
        mean_val_auc=mean_auc,
    )
    meta_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return final_model, report


def load_sequence_model(model_path: str | Path) -> tuple[Any, dict[str, Any]]:
    if not torch_available():
        raise RuntimeError("PyTorch required")
    import torch

    payload = torch.load(str(model_path), map_location="cpu", weights_only=False)
    kind = str(payload.get("model_kind", "lstm"))
    n_features = int(payload.get("n_features", FEATURE_COUNT))
    model = _build_model(kind, n_features, dropout=0.0)  # type: ignore[arg-type]
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, payload


def predict_sequence_proba(model: Any, X: np.ndarray) -> np.ndarray:
    import torch

    with torch.no_grad():
        t = torch.tensor(X, dtype=torch.float32)
        if t.ndim == 2:
            t = t.unsqueeze(0)
        return model(t).cpu().numpy().astype(float)
