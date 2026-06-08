from __future__ import annotations

import pytest

from deepsignal.live_trading.kis_stock_auto_execute_policy import (
    is_kis_stock_auto_execute_without_approval,
    load_kis_stock_auto_execute_config_from_env,
    should_notify_kis_plan_with_no_orders,
    should_skip_kis_telegram_approval,
)


def test_kis_stock_auto_execute_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_KIS_STOCK_AUTO_EXECUTE", raising=False)
    assert not is_kis_stock_auto_execute_without_approval()

    monkeypatch.setenv("KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", "true")
    cfg = load_kis_stock_auto_execute_config_from_env()
    assert cfg.enabled
    assert should_skip_kis_telegram_approval()
    assert not should_notify_kis_plan_with_no_orders()


def test_kis_stock_inactive_window_still_skips_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", raising=False)
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_AUTO_EXECUTE", "true")
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_START", "20:00")
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_END", "09:00")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from deepsignal.live_trading.operator_inactive_window import (
        OperatorInactiveConfig,
        is_inactive_auto_execute_active,
    )

    kst = ZoneInfo("Asia/Seoul")
    op_cfg = OperatorInactiveConfig(enabled=True)
    assert is_inactive_auto_execute_active(datetime(2026, 5, 21, 22, 0, tzinfo=kst), config=op_cfg)
    monkeypatch.setattr(
        "deepsignal.live_trading.kis_stock_auto_execute_policy.is_inactive_auto_execute_active",
        lambda *a, **k: True,
    )
    assert should_skip_kis_telegram_approval()
