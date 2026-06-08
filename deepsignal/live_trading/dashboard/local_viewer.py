"""로컬 운영 리포트 viewer ([실전-23]). 웹서버·네트워크·주문 기능 없음."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class LocalViewItem:
    name: str
    path: str
    exists: bool
    kind: str


@dataclass
class LocalViewerResult:
    output_dir: str
    items: list[LocalViewItem]
    opened: list[str]
    warnings: list[str]


DEFAULT_REPORTS: tuple[tuple[str, str, str], ...] = (
    ("OPS Dashboard", "OPS_DASHBOARD.html", "html"),
    ("Report Index", "REPORT_INDEX.html", "html"),
    ("Archive Viewer", "ARCHIVE_VIEWER.html", "html"),
    ("Archive Viewer CSV", "ARCHIVE_VIEWER.csv", "csv"),
    ("Archive Viewer Summary", "ARCHIVE_VIEWER_SUMMARY.md", "markdown"),
    ("Archive Viewer Presets", "ARCHIVE_VIEWER_PRESETS.json", "json"),
    ("Safety Audit", "SAFETY_AUDIT.md", "markdown"),
    ("AI Daily Trade Plan", "AI_DAILY_TRADE_PLAN.md", "markdown"),
    ("Latest AI Order Plan", "live_order_plan_ai_latest.json", "json"),
    ("AI Daily Trade Report", "AI_DAILY_TRADE_REPORT.md", "markdown"),
    ("AI Daily Status", "AI_DAILY_STATUS.md", "markdown"),
    ("Daily Summary", "DAILY_OPS_SUMMARY.md", "markdown"),
    ("Ops Dry Run", "OPS_DRY_RUN.md", "markdown"),
    ("Risk Alert", "RISK_ALERT.md", "markdown"),
    ("Sell Plan", "SELL_PLAN.md", "markdown"),
)

NAME_ALIASES = {
    "ops_dashboard": "OPS Dashboard",
    "dashboard": "OPS Dashboard",
    "ops": "OPS Dashboard",
    "report_index": "Report Index",
    "index": "Report Index",
    "archive": "Archive Viewer",
    "archive_viewer": "Archive Viewer",
    "safety": "Safety Audit",
    "safety_audit": "Safety Audit",
}


def _is_url_like(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and parsed.scheme not in {"", "file"})


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def collect_view_items(output_dir: str | Path = "outputs") -> list[LocalViewItem]:
    """기본 운영 리포트 목록을 수집한다. 파일 내용은 읽지 않는다."""
    root = Path(output_dir)
    items: list[LocalViewItem] = []
    for name, filename, kind in DEFAULT_REPORTS:
        path = root / filename
        items.append(
            LocalViewItem(
                name=name,
                path=path.as_posix(),
                exists=path.is_file(),
                kind=kind,
            )
        )
    latest_safety_json = sorted(p for p in root.glob("safety_audit_*.json") if p.is_file())
    json_path = latest_safety_json[-1] if latest_safety_json else root / "safety_audit_*.json"
    items.append(
        LocalViewItem(
            name="Safety Audit JSON",
            path=json_path.as_posix(),
            exists=json_path.is_file(),
            kind="json",
        )
    )
    latest_archive_json = sorted(p for p in root.glob("archive_viewer_*.json") if p.is_file())
    archive_json_path = latest_archive_json[-1] if latest_archive_json else root / "archive_viewer_*.json"
    items.append(
        LocalViewItem(
            name="Archive Viewer JSON",
            path=archive_json_path.as_posix(),
            exists=archive_json_path.is_file(),
            kind="json",
        )
    )
    for title, pattern in (
        ("AI Daily Trade Plan JSON", "ai_daily_trade_plan_*.json"),
        ("AI Daily Trade Report JSON", "ai_daily_trade_report_*.json"),
        ("AI Daily Status JSON", "ai_daily_status_*.json"),
    ):
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
        path = matches[-1] if matches else root / pattern
        items.append(LocalViewItem(name=title, path=path.as_posix(), exists=path.is_file(), kind="json"))
    return items


def open_local_report(path: str | Path, *, output_dir: str | Path | None = None) -> str:
    """검증된 로컬 파일을 기본 브라우저로 연다."""
    raw = str(path)
    if _is_url_like(raw):
        raise ValueError("external URLs are not allowed")

    target = Path(path)
    root = Path(output_dir) if output_dir is not None else target.parent
    if not _is_within(target, root):
        raise ValueError("path is outside output_dir")
    if not target.is_file():
        raise FileNotFoundError(target.as_posix())

    uri = target.resolve().as_uri()
    webbrowser.open(uri)
    return target.as_posix()


def _wanted_names(open_names: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    out: set[str] = set()
    for name in open_names or []:
        key = str(name).strip()
        out.add(NAME_ALIASES.get(key, key))
    return out


def build_local_viewer_result(
    output_dir: str | Path = "outputs",
    open_names: list[str] | tuple[str, ...] | set[str] | None = None,
    open_all: bool = False,
) -> LocalViewerResult:
    """리포트 목록을 만들고 요청된 로컬 HTML 리포트만 연다."""
    root = Path(output_dir)
    items = collect_view_items(root)
    opened: list[str] = []
    warnings: list[str] = []
    wanted = _wanted_names(open_names)

    for item in items:
        should_open = False
        if open_all:
            should_open = item.kind == "html" and item.exists
        elif item.name in wanted:
            should_open = True

        if not should_open:
            continue
        if item.kind != "html":
            warnings.append(f"Skip non-HTML report: {item.path}")
            continue
        if not item.exists:
            warnings.append(f"Missing report: {item.path}")
            continue
        try:
            opened.append(open_local_report(item.path, output_dir=root))
        except (OSError, ValueError) as e:
            warnings.append(f"Failed to open {item.path}: {e}")

    return LocalViewerResult(
        output_dir=root.as_posix(),
        items=items,
        opened=opened,
        warnings=warnings,
    )


def format_local_viewer_console(result: LocalViewerResult) -> str:
    lines = ["DeepSignal local viewer", "Available reports:"]
    for item in result.items:
        mark = "OK" if item.exists else "MISSING"
        lines.append(f"[{mark}] {item.name}: {item.path}")
    try:
        from deepsignal.live_trading.daily_ai_freshness import build_daily_ai_freshness, freshness_label_ko

        from deepsignal.live_trading.daily_ai_freshness import freshness_source_label_ko

        freshness = build_daily_ai_freshness(result.output_dir)
        lines.append("AI Daily Freshness:")
        for key in ("plan", "latest_order_plan", "report"):
            entry = freshness.get(key)
            if entry is None:
                continue
            label = freshness_label_ko(entry.status)
            source = freshness_source_label_ko(entry.freshness_source)
            stale_mark = " STALE" if entry.status == "STALE" else ""
            at = entry.generated_at or "-"
            lines.append(f"- {key}: {label}{stale_mark} · source={source} · at={at}")
    except Exception:
        pass
    try:
        from deepsignal.live_trading.archive_viewer import load_archive_viewer_link_info
        from deepsignal.live_trading.operator_labels import label_freshness_source

        archive = load_archive_viewer_link_info(result.output_dir)
        if isinstance(archive.freshness_source_summary, dict) and archive.freshness_source_summary:
            lines.append("Archive Viewer freshness summary:")
            for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown"):
                lines.append(f"- {label_freshness_source(key)}: {archive.freshness_source_summary.get(key, 0)}")
    except Exception:
        pass
    lines.append("Opened:")
    if result.opened:
        lines.extend(f"- {path}" for path in result.opened)
    else:
        lines.append("- (none)")
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    lines.append("Note: 로컬 file:// 리포트만 엽니다. 웹서버, 네트워크 호출, 주문 실행은 없습니다.")
    return "\n".join(lines)
