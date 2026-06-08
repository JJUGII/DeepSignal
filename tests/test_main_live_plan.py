"""live-plan CLI 스모크 (브로커 호출 없음)."""

from __future__ import annotations

from pathlib import Path

import main as main_mod


def test_main_live_plan_smoke(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, object]] = []

    def fake_run(db_path, cfg, *, output_dir="outputs"):
        calls.append((db_path, cfg))
        p = Path(output_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "stub.txt").write_text("ok", encoding="utf-8")

    db = tmp_path / "lp.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.live_trading import live_order_plan as lop_mod

    monkeypatch.setattr(lop_mod, "run_live_plan_cli", fake_run)
    main_mod.main(["live-plan", "--capital", "300000"])
    assert len(calls) == 1
    assert calls[0][1].capital == 300_000.0
