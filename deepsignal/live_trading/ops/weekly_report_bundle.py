"""Weekly report bundle creator ([실전-28])."""

from __future__ import annotations

import html
import json
import shutil
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class WeeklyReportBundleItem:
    source_path: str
    bundle_path: str
    category: str
    copied: bool
    reason: str


@dataclass
class WeeklyReportBundleResult:
    generated_at: str
    bundle_dir: str
    items: list[WeeklyReportBundleItem]
    status: str
    warnings: list[str]
    index_html: str | None
    index_md: str | None
    zip_path: str | None


WEEKLY_BUNDLE_OK = "WEEKLY_BUNDLE_OK"
WEEKLY_BUNDLE_WARNING = "WEEKLY_BUNDLE_WARNING"
WEEKLY_BUNDLE_NO_DATA = "WEEKLY_BUNDLE_NO_DATA"

STATIC_TARGETS: tuple[tuple[str, str], ...] = (
    ("WEEKLY_MAINTENANCE.md", "weekly_maintenance"),
    ("REPORT_HEALTH.md", "report_health"),
    ("REPORT_INDEX.html", "report_index"),
    ("REPORT_INDEX.md", "report_index"),
    ("OPS_DASHBOARD.html", "html_dashboard"),
    ("DAILY_OPS_SUMMARY.md", "daily_summary"),
    ("RISK_ALERT.md", "risk"),
    ("SELL_PLAN.md", "sell_plan"),
    ("OPS_DRY_RUN.md", "ops_dry_run"),
)

LATEST_TARGETS: tuple[tuple[str, str], ...] = (
    ("weekly_maintenance_*.json", "weekly_maintenance"),
    ("report_health_*.json", "report_health"),
    ("report_index_*.json", "report_index"),
    ("notification_audit_*.json", "notification"),
    ("report_cleanup_audit_*.json", "cleanup"),
)

SENSITIVE_NAMES = {".env", ".kis_token_cache.json"}
SENSITIVE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".py"}


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _latest(root: Path, pattern: str) -> Path | None:
    paths = [p for p in root.glob(pattern) if p.is_file()]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def _safe_report_file(path: Path, output_root: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    if not _is_inside(path, output_root):
        return False, "outside output_dir"
    if path.name in SENSITIVE_NAMES:
        return False, "sensitive file excluded"
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return False, "sensitive suffix excluded"
    if path.name.startswith("._"):
        return False, "AppleDouble metadata excluded"
    if path.suffix.lower() not in {".md", ".html", ".json"}:
        return False, "unsupported report suffix"
    return True, "ok"


def _unique_target(bundle_dir: Path, name: str) -> Path:
    target = bundle_dir / name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 1000):
        alt = bundle_dir / f"{stem}_{i}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError(f"Could not choose unique bundle path for {name}")


def _copy_report(path: Path, bundle_dir: Path, output_root: Path, category: str) -> WeeklyReportBundleItem:
    safe, reason = _safe_report_file(path, output_root)
    if not safe:
        return WeeklyReportBundleItem(
            source_path=path.as_posix(),
            bundle_path="",
            category=category,
            copied=False,
            reason=reason,
        )
    target = _unique_target(bundle_dir, path.name)
    shutil.copy2(path, target)
    return WeeklyReportBundleItem(
        source_path=path.as_posix(),
        bundle_path=target.as_posix(),
        category=category,
        copied=True,
        reason="copied",
    )


def _collect_targets(output_root: Path, warnings: list[str]) -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    for name, category in STATIC_TARGETS:
        path = output_root / name
        if path.is_file():
            targets.append((path, category))
            seen.add(path.resolve())
        elif name != "OPS_DRY_RUN.md":
            warnings.append(f"Missing bundle target: {name}")

    for pattern, category in LATEST_TARGETS:
        path = _latest(output_root, pattern)
        if path is None:
            warnings.append(f"No latest file for {pattern}")
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        targets.append((path, category))
        seen.add(resolved)
    return targets


def _render_markdown(result: WeeklyReportBundleResult) -> str:
    lines = [
        "# DeepSignal Weekly Report Bundle",
        "",
        "## Summary",
        "",
        f"- Status: **{result.status}**",
        f"- Generated at: {result.generated_at}",
        f"- Bundle dir: `{result.bundle_dir}`",
        f"- Item count: {sum(1 for item in result.items if item.copied)}",
        f"- ZIP: `{result.zip_path}`" if result.zip_path else "- ZIP: (not created)",
        "",
        "## Files",
        "",
        "| Category | File | Status |",
        "|----------|------|--------|",
    ]
    for item in result.items:
        name = Path(item.bundle_path or item.source_path).name
        status = "copied" if item.copied else item.reason
        if item.copied:
            lines.append(f"| {item.category} | [{name}]({name}) | {status} |")
        else:
            lines.append(f"| {item.category} | {name} | {status} |")
    lines.extend(["", "## Warnings", ""])
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This bundle excludes `.env`, token cache, DB files, and source code.",
            "- This command does not delete files, move archives, call networks, send alerts, or place orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_html(result: WeeklyReportBundleResult) -> str:
    rows = []
    for item in result.items:
        name = Path(item.bundle_path or item.source_path).name
        status = "copied" if item.copied else item.reason
        if item.copied:
            file_cell = f'<a href="{html.escape(name)}">{html.escape(name)}</a>'
        else:
            file_cell = html.escape(name)
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.category)}</td>"
            f"<td>{file_cell}</td>"
            f"<td>{html.escape(status)}</td>"
            "</tr>"
        )
    warnings = "".join(f"<li>{html.escape(w)}</li>" for w in result.warnings) or "<li>(none)</li>"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>DeepSignal Weekly Report Bundle</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;color:#20242a}"
        "table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left}"
        "th{background:#f3f4f6}code{background:#f3f4f6;padding:2px 5px;border-radius:4px}</style></head><body>"
        "<h1>DeepSignal Weekly Report Bundle</h1>"
        f"<p>Status: <strong>{html.escape(result.status)}</strong></p>"
        f"<p>Generated at: {html.escape(result.generated_at)}</p>"
        f"<p>Bundle dir: <code>{html.escape(result.bundle_dir)}</code></p>"
        "<h2>Files</h2><table><thead><tr><th>Category</th><th>File</th><th>Status</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        "<h2>Warnings</h2><ul>"
        + warnings
        + "</ul><h2>Safety</h2><p>No delete, archive move, network call, alert send, order, SELL automation, or KIS POST.</p>"
        "</body></html>"
    )


def _write_indexes(result: WeeklyReportBundleResult, bundle_dir: Path) -> tuple[Path, Path]:
    md = bundle_dir / "BUNDLE_INDEX.md"
    hp = bundle_dir / "BUNDLE_INDEX.html"
    md.write_text(_render_markdown(result), encoding="utf-8")
    hp.write_text(_render_html(result), encoding="utf-8")
    return hp, md


def _write_zip(bundle_dir: Path) -> Path:
    zip_path = bundle_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in bundle_dir.rglob("*") if p.is_file()):
            zf.write(path, path.relative_to(bundle_dir).as_posix())
    return zip_path


def create_weekly_report_bundle(
    *,
    output_dir: str | Path = "outputs",
    bundle_dir: str | Path = "outputs/weekly_bundles",
    create_zip: bool = False,
    run_weekly: bool = True,
    db_path: str | Path = "data/deepsignal.db",
) -> WeeklyReportBundleResult:
    """Create a local weekly report bundle. No delete/archive/network/send/order."""
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    bundle_root = Path(bundle_dir).expanduser().resolve()
    if not _is_inside(bundle_root, output_root):
        raise ValueError("bundle_dir must be inside output_dir")
    bundle_root.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    base_name = f"weekly_bundle_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}"
    actual_bundle_dir = bundle_root / base_name
    if actual_bundle_dir.exists():
        for i in range(1, 1000):
            candidate = bundle_root / f"{base_name}_{i}"
            if not candidate.exists():
                actual_bundle_dir = candidate
                break
    actual_bundle_dir.mkdir(parents=True, exist_ok=False)
    warnings: list[str] = []

    if run_weekly:
        from deepsignal.live_trading.html_dashboard import write_html_dashboard
        from deepsignal.live_trading.notification_center import notify_alerts
        from deepsignal.live_trading.report_index import run_report_index
        from deepsignal.live_trading.weekly_maintenance import run_weekly_maintenance, write_weekly_maintenance_report

        weekly = run_weekly_maintenance(output_dir=output_root, archive_dir=output_root / "archive", db_path=db_path)
        write_weekly_maintenance_report(weekly, output_dir=output_root)
        notify_alerts(output_dir=output_root, dry_run=True, include_maintenance=True)
        run_report_index(output_dir=output_root, archive_dir=output_root / "archive")
        write_html_dashboard(output_dir=output_root)

    items: list[WeeklyReportBundleItem] = []
    for path, category in _collect_targets(output_root, warnings):
        item = _copy_report(path, actual_bundle_dir, output_root, category)
        if not item.copied:
            warnings.append(f"Skipped {path.name}: {item.reason}")
        items.append(item)

    copied_count = sum(1 for item in items if item.copied)
    status = WEEKLY_BUNDLE_NO_DATA if copied_count == 0 else (WEEKLY_BUNDLE_WARNING if warnings else WEEKLY_BUNDLE_OK)
    result = WeeklyReportBundleResult(
        generated_at=now.isoformat(timespec="seconds"),
        bundle_dir=actual_bundle_dir.as_posix(),
        items=items,
        status=status,
        warnings=warnings,
        index_html=None,
        index_md=None,
        zip_path=None,
    )
    hp, md = _write_indexes(result, actual_bundle_dir)
    result.index_html = hp.as_posix()
    result.index_md = md.as_posix()
    # Re-render indexes with their own paths populated.
    _write_indexes(result, actual_bundle_dir)
    if create_zip:
        result.zip_path = _write_zip(actual_bundle_dir).as_posix()
        _write_indexes(result, actual_bundle_dir)
    return result


def open_weekly_bundle(result: WeeklyReportBundleResult) -> bool:
    if not result.index_html:
        return False
    return bool(webbrowser.open(Path(result.index_html).resolve().as_uri()))


def format_weekly_report_bundle_console(result: WeeklyReportBundleResult) -> str:
    lines = [
        "DeepSignal weekly report bundle",
        f"Status: {result.status}",
        f"Bundle dir: {result.bundle_dir}",
        f"Items: {sum(1 for item in result.items if item.copied)}",
        f"Index HTML: {result.index_html or '-'}",
        f"Index Markdown: {result.index_md or '-'}",
        f"ZIP: {result.zip_path or '-'}",
    ]
    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    lines.append("Note: weekly-report-bundle copies local reports only; no delete, archive move, network, send, or orders.")
    return "\n".join(lines)
