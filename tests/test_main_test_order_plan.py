"""main.py generate-test-order-plan CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod


def test_main_generate_test_order_plan_smoke(tmp_path: Path, capsys) -> None:
    rc = main_mod.main(
        [
            "generate-test-order-plan",
            "--symbol",
            "005930",
            "--quantity",
            "1",
            "--limit-price",
            "70000",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert rc == 0
    path = tmp_path / "test_live_order_plan.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["orders"][0]["estimated_order_value"] == 70_000.0
    out = capsys.readouterr().out
    assert "telegram-approval-request" in out
