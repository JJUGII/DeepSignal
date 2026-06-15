"""DM6 레짐 마스터게이트 테스트."""

from __future__ import annotations

import os
from unittest import mock

from deepsignal.risk import regime_gate as rg
from deepsignal.live_trading.regime_trend import RegimeTrendSignal

_SIG = "deepsignal.live_trading.regime_trend.compute_trend_signal"


def _sig(in_mkt: bool, source: str = "economic_indicators.SP500", reason: str = "r") -> RegimeTrendSignal:
    return RegimeTrendSignal(
        in_market=in_mkt, index_close=100.0, sma200=90.0,
        asof="2026-06-05", source=source, reason=reason,
    )


def test_in_market_allows():
    with mock.patch(_SIG, return_value=_sig(True)):
        ok, _ = rg.regime_allows_long_buy("/tmp", asset="crypto")
    assert ok is True


def test_risk_off_blocks():
    with mock.patch(_SIG, return_value=_sig(False, reason="종가 ≤ SMA200")):
        ok, why = rg.regime_allows_long_buy("/tmp", asset="crypto")
    assert ok is False
    assert "risk-off" in why and "crypto" in why


def test_missing_data_fail_open():
    # 데이터 공백은 매매를 벽돌화하지 않는다 — 통과(fail-open).
    with mock.patch(_SIG, return_value=_sig(False, source="insufficient", reason="봉 부족")):
        ok, _ = rg.regime_allows_long_buy("/tmp")
    assert ok is True


def test_signal_exception_fail_open():
    with mock.patch(_SIG, side_effect=RuntimeError("db down")):
        ok, _ = rg.regime_allows_long_buy("/tmp")
    assert ok is True


def test_env_disable():
    with mock.patch.dict(os.environ, {"DEEPSIGNAL_ENFORCE_REGIME_GATE": "false"}):
        with mock.patch(_SIG, return_value=_sig(False)):
            ok, _ = rg.regime_allows_long_buy("/tmp")
    assert ok is True
