"""outputs 리포트 보존/정리 매니저 ([실전-20]). 파일 정리 전용."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class CleanupCandidate:
    path: str
    category: str
    modified_at: str
    size_bytes: int
    reason: str
    action: str


@dataclass
class CleanupResult:
    dry_run: bool
    output_dir: str
    deleted: list[CleanupCandidate]
    archived: list[CleanupCandidate]
    kept: list[CleanupCandidate]
    warnings: list[str]
    audit_path: str | None


PROTECTED_NAMES = {
    "OPS_DASHBOARD.html",
    "OPS_DASHBOARD.md",
    "DAILY_OPS_SUMMARY.md",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
    "LIVE_ACCOUNT_SNAPSHOT.md",
    "RECONCILE_LIVE_ACCOUNT.md",
    ".gitkeep",
    ".kis_token_cache.json",
}

REPORT_PREFIXES = {
    "live_account_snapshot": "live_account_snapshot",
    "reconcile_live_account": "reconcile_live_account",
    "risk_alert": "risk_alert",
    "ops_dashboard": "ops_dashboard",
    "sell_plan": "sell_plan",
    "notification_audit": "notification_audit",
    "daily_ops_summary": "daily_ops_summary",
    "pre_trade_runbook": "pre_trade_runbook",
    "post_trade_runbook": "post_trade_runbook",
    "live_fill_summary": "live_fill_summary",
    "live_order_status": "live_order_status",
    "kis_debug_account": "kis_debug_account",
    "report_cleanup_audit": "report_cleanup_audit",
}

TIMESTAMPED_JSON_RE = re.compile(r"^.+_\d{8}_\d{6}\.json$")


def _resolve_output_dir(output_dir: str | Path) -> Path:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _category(path: Path) -> str | None:
    name = path.name
    if name.startswith("._"):
        return "appledouble"
    for prefix, category in REPORT_PREFIXES.items():
        if name.startswith(prefix + "_") and name.endswith(".json"):
            return category
    if TIMESTAMPED_JSON_RE.match(name):
        return "timestamped_json"
    return None


def _candidate(path: Path, root: Path, category: str, reason: str, action: str) -> CleanupCandidate:
    st = path.stat()
    return CleanupCandidate(
        path=path.relative_to(root).as_posix(),
        category=category,
        modified_at=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        size_bytes=int(st.st_size),
        reason=reason,
        action=action,
    )


def _scan_report_files(root: Path, *, remove_appledouble: bool) -> tuple[dict[str, list[Path]], list[CleanupCandidate]]:
    by_category: dict[str, list[Path]] = {}
    apple_kept: list[CleanupCandidate] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if not _is_inside(path, root):
            continue
        rel_parts = path.relative_to(root).parts
        if "archive" in rel_parts:
            continue
        if path.name in PROTECTED_NAMES:
            continue
        category = _category(path)
        if category is None:
            continue
        if category == "appledouble" and not remove_appledouble:
            # Still include it as a dry-run candidate so operators can see compileall blockers.
            pass
        by_category.setdefault(category, []).append(path)
    return by_category, apple_kept


def _choose_candidates(
    by_category: dict[str, list[Path]],
    root: Path,
    *,
    keep_days: int,
    keep_latest: int,
    archive: bool,
) -> tuple[list[CleanupCandidate], list[CleanupCandidate]]:
    now = datetime.now()
    cutoff = now - timedelta(days=max(0, int(keep_days)))
    candidates: list[CleanupCandidate] = []
    kept: list[CleanupCandidate] = []
    for category, paths in by_category.items():
        sorted_paths = sorted(paths, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        latest_keep = set(sorted_paths[: max(0, int(keep_latest))])
        for path in sorted_paths:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            if category != "appledouble" and path in latest_keep:
                kept.append(_candidate(path, root, category, f"latest {keep_latest} kept for category", "keep"))
                continue
            if category != "appledouble" and modified >= cutoff:
                kept.append(_candidate(path, root, category, f"modified within keep_days={keep_days}", "keep"))
                continue
            action = "archive" if archive and category != "appledouble" else "delete"
            reason = "AppleDouble metadata cleanup" if category == "appledouble" else "older than retention policy"
            candidates.append(_candidate(path, root, category, reason, action))
    return candidates, kept


def _archive_path(path: Path, root: Path, archive_root: Path) -> Path:
    rel = path.relative_to(root)
    target = archive_root / rel
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 1000):
        alt = target.with_name(f"{stem}_{i}{suffix}")
        if not alt.exists():
            return alt
    raise RuntimeError(f"Could not choose archive path for {path}")


def _write_audit(
    root: Path,
    *,
    dry_run: bool,
    keep_days: int,
    keep_latest: int,
    archive: bool,
    candidates: list[CleanupCandidate],
    deleted: list[CleanupCandidate],
    archived: list[CleanupCandidate],
    kept: list[CleanupCandidate],
    warnings: list[str],
) -> Path:
    now = datetime.now()
    path = root / f"report_cleanup_audit_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    body: dict[str, Any] = {
        "dry_run": dry_run,
        "keep_days": keep_days,
        "keep_latest": keep_latest,
        "archive": archive,
        "candidates": [asdict(x) for x in candidates],
        "deleted": [asdict(x) for x in deleted],
        "archived": [asdict(x) for x in archived],
        "kept": [asdict(x) for x in kept],
        "warnings": warnings,
        "actual_order_attempted": False,
        "실제_주문_없음": True,
        "network_called": False,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def cleanup_reports(
    *,
    output_dir: str | Path = "outputs",
    keep_days: int = 14,
    keep_latest: int = 20,
    archive: bool = False,
    archive_dir: str | Path | None = None,
    remove_appledouble: bool = False,
    dry_run: bool = True,
) -> CleanupResult:
    """outputs 내부 리포트만 dry-run/삭제/archive한다. 네트워크·주문 호출 없음."""
    root = _resolve_output_dir(output_dir)
    warnings: list[str] = []
    archive_root: Path | None = None
    if archive:
        archive_root = (Path(archive_dir) if archive_dir else root / "archive").expanduser().resolve()
        try:
            archive_root.relative_to(root)
        except ValueError:
            raise ValueError("archive_dir must be inside output_dir")

    by_category, apple_kept = _scan_report_files(root, remove_appledouble=remove_appledouble)
    candidates, kept = _choose_candidates(
        by_category,
        root,
        keep_days=keep_days,
        keep_latest=keep_latest,
        archive=archive,
    )
    kept.extend(apple_kept)
    deleted: list[CleanupCandidate] = []
    archived: list[CleanupCandidate] = []

    if not dry_run:
        for cand in candidates:
            path = (root / cand.path).resolve()
            if not _is_inside(path, root):
                warnings.append(f"Skipped outside output_dir: {cand.path}")
                continue
            if path.name in PROTECTED_NAMES:
                warnings.append(f"Skipped protected file: {cand.path}")
                continue
            if cand.category == "appledouble" and not remove_appledouble:
                warnings.append(f"Skipped AppleDouble without --remove-appledouble: {cand.path}")
                kept.append(
                    CleanupCandidate(
                        path=cand.path,
                        category=cand.category,
                        modified_at=cand.modified_at,
                        size_bytes=cand.size_bytes,
                        reason="AppleDouble requires --remove-appledouble",
                        action="keep",
                    )
                )
                continue
            if cand.action == "archive":
                assert archive_root is not None
                target = _archive_path(path, root, archive_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path.as_posix(), target.as_posix())
                archived.append(
                    CleanupCandidate(
                        path=target.relative_to(root).as_posix(),
                        category=cand.category,
                        modified_at=cand.modified_at,
                        size_bytes=cand.size_bytes,
                        reason=cand.reason,
                        action="archive",
                    )
                )
            else:
                path.unlink()
                deleted.append(cand)

    audit_path = _write_audit(
        root,
        dry_run=dry_run,
        keep_days=keep_days,
        keep_latest=keep_latest,
        archive=archive,
        candidates=candidates,
        deleted=deleted,
        archived=archived,
        kept=kept,
        warnings=warnings,
    )
    return CleanupResult(
        dry_run=dry_run,
        output_dir=root.as_posix(),
        deleted=deleted,
        archived=archived,
        kept=kept,
        warnings=warnings,
        audit_path=audit_path.as_posix(),
    )
