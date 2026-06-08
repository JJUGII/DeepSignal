from __future__ import annotations

from pathlib import Path

import main as main_mod
from deepsignal.live_trading.approved_execution import ApprovedExecutionResult


def _result(tmp_path: Path, *, request_id: str = "REQ123", success: bool = True) -> ApprovedExecutionResult:
    audit = tmp_path / "execute_approved_audit_test.json"
    md = tmp_path / "EXECUTE_APPROVED_AUDIT.md"
    audit.write_text("{}", encoding="utf-8")
    md.write_text("# audit\n", encoding="utf-8")
    return ApprovedExecutionResult(
        request_id=request_id,
        success=success,
        status="EXECUTE_APPROVED_COMPLETED" if success else "EXECUTE_APPROVED_BLOCKED",
        errors=[] if success else ["blocked"],
        warnings=[],
        audit_json_path=audit.as_posix(),
        audit_markdown_path=md.as_posix(),
        live_approval_audit_path=(tmp_path / "live_approval_audit_test.json").as_posix(),
        execution_result={"status": "KIS_LIVE_ORDER_COMPLETED" if success else "LIVE_EXECUTION_BLOCKED"},
    )


def test_main_execute_last_approved_smoke(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import approved_execution as ae

    called: dict[str, object] = {}

    def fake_execute_last_approved(**kwargs):
        called.update(kwargs)
        return _result(tmp_path)

    monkeypatch.setattr(ae, "execute_last_approved", fake_execute_last_approved)

    rc = main_mod.main(["execute-last-approved", "--output-dir", str(tmp_path)])

    assert rc == 0
    assert called["output_dir"] == str(tmp_path)
    assert "final_confirm" not in called


def test_main_execute_approved_request_id_smoke(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import approved_execution as ae

    called: dict[str, object] = {}

    def fake_execute_approved_by_request_id(**kwargs):
        called.update(kwargs)
        return _result(tmp_path, request_id=kwargs["request_id"])

    monkeypatch.setattr(ae, "execute_approved_by_request_id", fake_execute_approved_by_request_id)

    rc = main_mod.main(["execute-approved", "--request-id", "REQ777", "--output-dir", str(tmp_path)])

    assert rc == 0
    assert called["request_id"] == "REQ777"
    assert called["output_dir"] == str(tmp_path)
    assert "final_confirm" not in called


def test_main_execute_approved_missing_request_blocks(tmp_path: Path) -> None:
    rc = main_mod.main(["execute-approved", "--request-id", "MISSING", "--output-dir", str(tmp_path)])

    assert rc == 1
