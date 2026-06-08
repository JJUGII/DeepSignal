"""Append-only retrain audit log (outputs/retrain_history.jsonl)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RETRAIN_HISTORY_FILENAME = "retrain_history.jsonl"


def retrain_history_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / RETRAIN_HISTORY_FILENAME


@dataclass
class RetrainHistoryEntry:
    date: str
    val_auc: float
    val_sharpe: float
    train_sharpe: float
    deployed: bool
    n_trades_used: int
    model_version: str
    reason: str = ""
    also_seq: bool = False
    warm_start: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_retrain_history(
    output_dir: str | Path,
    entry: RetrainHistoryEntry | dict[str, Any],
) -> Path:
    path = retrain_history_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = entry.to_dict() if isinstance(entry, RetrainHistoryEntry) else dict(entry)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def load_retrain_history(
    output_dir: str | Path,
    *,
    last_days: int = 30,
) -> list[dict[str, Any]]:
    path = retrain_history_path(output_dir)
    if not path.is_file():
        return []
    cutoff = datetime.now() - timedelta(days=int(last_days))
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            dt = datetime.fromisoformat(str(row.get("date", "")).replace("Z", "+00:00")[:19])
            if dt.replace(tzinfo=None) >= cutoff.replace(tzinfo=None):
                rows.append(row)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return rows


def format_retrain_history_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No retrain history in the last 30 days."
    lines = [
        "date               | val_auc | val_sharpe | train_sharpe | deployed | n_trades | version",
        "-------------------+---------+------------+--------------+----------+----------+--------",
    ]
    for r in rows[-30:]:
        lines.append(
            f"{str(r.get('date', ''))[:19]:19} | "
            f"{float(r.get('val_auc', 0)):7.3f} | "
            f"{float(r.get('val_sharpe', 0)):10.2f} | "
            f"{float(r.get('train_sharpe', 0)):12.2f} | "
            f"{'yes' if r.get('deployed') else 'no':8} | "
            f"{int(r.get('n_trades_used', 0)):8} | "
            f"{r.get('model_version', '')}"
        )
    return "\n".join(lines)
