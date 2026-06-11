from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.risk.edge_gate import edge_gate_allows_buy


def _write_gate(out: Path, *, crypto_deploy: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "EDGE_GATE.json").write_text(
        json.dumps(
            {
                "strategies": {
                    "crypto_scalp_5m": {
                        "edge": bool(crypto_deploy),
                        "deploy": bool(crypto_deploy),
                        "metrics": {},
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_crypto_edge_hard_blocks_even_when_global_gate_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_gate(tmp_path, crypto_deploy=False)
    monkeypatch.setenv("DEEPSIGNAL_ENFORCE_EDGE_GATE", "false")
    monkeypatch.delenv("DEEPSIGNAL_ALLOW_UNVERIFIED_CRYPTO_BUY", raising=False)

    ok, reason = edge_gate_allows_buy(tmp_path, "crypto_scalp_5m")

    assert ok is False
    assert "코인 엣지 하드차단" in reason


def test_crypto_edge_emergency_override_allows_unverified_buy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_gate(tmp_path, crypto_deploy=False)
    monkeypatch.setenv("DEEPSIGNAL_ENFORCE_EDGE_GATE", "false")
    monkeypatch.setenv("DEEPSIGNAL_ALLOW_UNVERIFIED_CRYPTO_BUY", "true")

    ok, reason = edge_gate_allows_buy(tmp_path, "crypto_scalp_5m")

    assert ok is True
    assert "미적용" in reason


def test_crypto_edge_deploy_allows_buy(tmp_path: Path) -> None:
    _write_gate(tmp_path, crypto_deploy=True)

    ok, reason = edge_gate_allows_buy(tmp_path, "crypto_scalp_5m")

    assert ok is True
    assert "deploy" in reason
