"""Manual operations checklist generator ([실전-29]).

This module writes reference Markdown only. It does not create cron, launchd,
plist, shell scheduler files, network calls, or order automation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChecklistItem:
    title: str
    command: str | None
    required: bool
    warning: str | None


@dataclass
class ChecklistDocument:
    name: str
    path: str
    items: list[ChecklistItem]
    warnings: list[str]


SAFETY_WARNINGS: list[str] = [
    "cron/launchd 자동 실주문 금지",
    "live-approve --execute 자동화 금지",
    "--final-confirm 자동 주입 금지",
    ".env 커밋 금지",
    "KIS_ENV=live 확인",
    "SELL 자동화 금지",
    "시장가 금지",
    "KIS POST 직접 호출 금지",
]

MANUAL_ONLY_WARNINGS: list[str] = [
    "이 문서는 운영자 참고용 체크리스트이며 실제 스케줄러가 아니다.",
    "자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않는다.",
    *SAFETY_WARNINGS,
]


def _item(title: str, command: str | None = None, *, required: bool = True, warning: str | None = None) -> ChecklistItem:
    return ChecklistItem(title=title, command=command, required=required, warning=warning)


def build_checklist_documents(output_dir: str | Path = "outputs/checklists") -> list[ChecklistDocument]:
    """Build checklist document models without writing files."""
    root = Path(output_dir)
    specs: list[tuple[str, list[ChecklistItem], list[str]]] = [
        (
            "DAILY_CHECKLIST.md",
            [
                _item("가상환경 활성화", "source .venv/bin/activate"),
                _item("거래 세션 확인", "python main.py trading-session-check"),
                _item("KIS 설정 확인", "python main.py kis-check"),
                _item("실계좌 스냅샷 동기화", "python main.py live-sync-account --broker kis --network"),
                _item("실계좌 DB 대조", "python main.py reconcile-live-account --broker kis --network"),
                _item("리스크 경고 확인", "python main.py risk-check --broker kis --output-dir outputs"),
                _item("운영 대시보드 생성", "python main.py ops-dashboard --output-dir outputs"),
                _item("HTML 대시보드 생성", "python main.py html-dashboard --output-dir outputs"),
                _item("대시보드 경로 확인/열기", "python main.py open-dashboard --output-dir outputs"),
            ],
            MANUAL_ONLY_WARNINGS,
        ),
        (
            "PRE_MARKET_CHECKLIST.md",
            [
                _item("KIS_ENV 확인", None, warning="`.env`의 KIS_ENV가 의도한 값인지 운영자가 직접 확인한다."),
                _item("거래 세션 확인", "python main.py trading-session-check"),
                _item("실계좌 스냅샷 동기화", "python main.py live-sync-account --broker kis --network"),
                _item("실계좌 DB 대조", "python main.py reconcile-live-account --broker kis --network"),
                _item("주문 전 runbook 실행", "python main.py pre-trade-runbook --broker kis --network --plan outputs/live_order_plan.json --symbol 005930 --quantity 1 --limit-price 70000 --allow-symbol 005930 --output-dir outputs"),
                _item("live-plan 검토", "python main.py live-plan --output-dir outputs"),
                _item("주문 실행은 수동 승인만", None, warning="`live-approve --execute`는 자동화하지 않고 운영자가 직접 최종 승인한다."),
            ],
            MANUAL_ONLY_WARNINGS,
        ),
        (
            "POST_TRADE_CHECKLIST.md",
            [
                _item("사후 runbook 및 요약 생성", "python main.py post-trade-runbook --broker kis --network --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json --with-summary --output-dir outputs"),
                _item("주문 상태 조회", "python main.py live-order-status --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json --network"),
                _item("체결 요약 확인", "python main.py live-fill-summary --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json"),
                _item("리스크 경고 확인", "python main.py risk-check --broker kis --output-dir outputs"),
                _item("운영 대시보드 생성", "python main.py ops-dashboard --output-dir outputs"),
                _item("수동 SELL 계획서 검토", "python main.py sell-plan --output-dir outputs", warning="SELL 계획서는 검토용이며 자동매도 명령이 아니다."),
                _item("알림 메시지 dry-run 확인", "python main.py notify-alerts --dry-run --output-dir outputs"),
            ],
            MANUAL_ONLY_WARNINGS,
        ),
        (
            "WEEKLY_MAINTENANCE_CHECKLIST.md",
            [
                _item("주간 maintenance dry-run", "python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive"),
                _item("리포트 health check", "python main.py report-health-check --output-dir outputs"),
                _item("리포트 cleanup 후보 확인", "python main.py cleanup-reports --output-dir outputs --dry-run"),
                _item("리포트 index 생성", "python main.py report-index --output-dir outputs --archive-dir outputs/archive"),
                _item("주간 리포트 bundle 생성", "python main.py weekly-report-bundle --output-dir outputs"),
                _item(
                    "cleanup-reports --apply는 수동 검토 후에만",
                    "python main.py cleanup-reports --output-dir outputs --apply --archive --archive-dir outputs/archive",
                    warning="cleanup audit의 candidates를 운영자가 확인한 뒤에만 수동 실행한다.",
                ),
            ],
            MANUAL_ONLY_WARNINGS,
        ),
        (
            "SAFETY_RULES.md",
            [],
            MANUAL_ONLY_WARNINGS,
        ),
    ]
    return [
        ChecklistDocument(name=name, path=(root / name).as_posix(), items=items, warnings=warnings)
        for name, items, warnings in specs
    ]


def _render_checklist(document: ChecklistDocument) -> str:
    title = document.name.removesuffix(".md").replace("_", " ").title()
    lines = [
        f"# DeepSignal {title}",
        "",
        "> Manual checklist only. This file is not a scheduler and does not run commands automatically.",
        "",
        "## Safety Warnings",
        "",
    ]
    for warning in document.warnings:
        lines.append(f"- {warning}")

    if document.items:
        lines.extend(["", "## Checklist", ""])
        for item in document.items:
            marker = "required" if item.required else "optional"
            lines.append(f"- [ ] **{item.title}** ({marker})")
            if item.command:
                lines.append(f"  - Command: `{item.command}`")
            if item.warning:
                lines.append(f"  - Warning: {item.warning}")
    else:
        lines.extend(["", "## Rules", ""])
        for warning in SAFETY_WARNINGS:
            lines.append(f"- {warning}")

    lines.extend(
        [
            "",
            "## Explicitly Not Included",
            "",
            "- No cron registration.",
            "- No launchd plist generation.",
            "- No automatic execution.",
            "- No network call from this generator.",
            "- No real order placement.",
            "- No SELL automation.",
            "- No KIS POST call.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_checklists(output_dir: str | Path = "outputs/checklists") -> list[ChecklistDocument]:
    """Write Markdown checklists and return generated document metadata."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    documents = build_checklist_documents(root)
    for document in documents:
        Path(document.path).write_text(_render_checklist(document), encoding="utf-8")
    return documents


def format_checklists_console(documents: list[ChecklistDocument]) -> str:
    lines = ["DeepSignal checklists generated"]
    lines.extend(f"- {document.path}" for document in documents)
    lines.append("Note: checklist generation only; no cron, launchd, automation, network, order, SELL, or KIS POST.")
    return "\n".join(lines)
