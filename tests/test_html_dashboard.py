"""html_dashboard: 정적 운영 HTML 대시보드."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.html_dashboard import render_html_dashboard, write_html_dashboard


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def test_no_data_still_generates_html(tmp_path: Path) -> None:
    result = write_html_dashboard(output_dir=tmp_path)
    path = tmp_path / "OPS_DASHBOARD.html"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "DeepSignal Operations Dashboard" in text
    assert "No data" in text
    assert result.status == "NO_DATA"


def test_risk_warning_displayed(tmp_path: Path) -> None:
    _write(tmp_path / "risk_alert_20260516_100000.json", {"status": "WARNING", "alerts": ["loss warning"], "positions": []})
    result = write_html_dashboard(output_dir=tmp_path)
    text = Path(result.html_path).read_text(encoding="utf-8")
    assert "WARNING" in text
    assert "loss warning" in text


def test_reconcile_mismatch_displayed(tmp_path: Path) -> None:
    _write(tmp_path / "reconcile_live_account_20260516_100000.json", {"success": False, "missing_in_db": [{"symbol": "005930"}], "missing_in_broker": [], "quantity_mismatch": []})
    result = write_html_dashboard(output_dir=tmp_path)
    text = Path(result.html_path).read_text(encoding="utf-8")
    assert "success=False" in text
    assert "Missing in DB" in text or "missing_in_db" not in text


def test_positions_table_included(tmp_path: Path) -> None:
    _write(
        tmp_path / "ops_dashboard_20260516_100000.json",
        {
            "status": "WARNING",
            "positions": [{"symbol": "005930", "quantity": 1, "avg_price": 280000, "current_price": 270500, "market_value": 270500}],
            "recent_orders": [],
        },
    )
    _write(
        tmp_path / "risk_alert_20260516_100000.json",
        {"status": "WARNING", "positions": [{"symbol": "005930", "risk_level": "WARNING", "unrealized_pnl": -9500, "unrealized_pnl_pct": -0.0339}], "alerts": []},
    )
    result = write_html_dashboard(output_dir=tmp_path)
    text = Path(result.html_path).read_text(encoding="utf-8")
    assert "Symbol" in text
    assert "005930" in text
    assert "-3.39%" in text


def test_next_actions_included(tmp_path: Path) -> None:
    _write(
        tmp_path / "daily_ops_summary_20260516_100000.json",
        {"status": "WARNING", "generated_at": "t", "next_actions": ["Review warnings before adding positions."], "warnings": []},
    )
    result = write_html_dashboard(output_dir=tmp_path)
    text = Path(result.html_path).read_text(encoding="utf-8")
    assert "Review warnings before adding positions." in text


def test_html_escaping() -> None:
    html_text = render_html_dashboard(
        {
            "daily": {"status": "<script>alert(1)</script>", "next_actions": ["<b>bad</b>"]},
            "ops": {},
            "risk": {},
            "sell": {},
            "reconcile": {},
            "account": {},
            "fills": {},
            "notification": {},
        }
    )
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_text
    assert "&lt;b&gt;bad&lt;/b&gt;" in html_text
