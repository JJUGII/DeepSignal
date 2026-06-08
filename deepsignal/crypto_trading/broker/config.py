"""Upbit API credentials from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled, effective_dry_run


class UpbitConfigError(ValueError):
    pass


_MOCK_KEYS = {"dry-run-key", "demo-key"}


@dataclass(frozen=True)
class UpbitConfig:
    access_key: str
    secret_key: str
    dry_run: bool = True
    paper_mode: bool = False

    @property
    def is_demo(self) -> bool:
        """키 없이 자동 전환된 데모/드라이런 모드."""
        return self.access_key in _MOCK_KEYS

    def masked_summary(self) -> dict[str, str | bool]:
        return {
            "access_key": "DEMO (no key)" if self.is_demo else (
                f"{self.access_key[:4]}…{self.access_key[-4:]}" if len(self.access_key) >= 8 else "(set)"
            ),
            "secret_key": "(set)" if self.secret_key and not self.is_demo else "(missing)",
            "dry_run": self.dry_run,
            "paper_mode": self.paper_mode,
        }


def load_upbit_config_from_env(*, dry_run: bool | None = None) -> UpbitConfig:
    paper = crypto_paper_mode_enabled()
    resolved_dry = effective_dry_run(requested_dry_run=dry_run)
    access = (os.environ.get("UPBIT_ACCESS_KEY") or "").strip()
    secret = (os.environ.get("UPBIT_SECRET_KEY") or "").strip()
    if not access or not secret:
        # 키 없으면 항상 데모 모드로 자동 전환 (크래시 없음)
        return UpbitConfig(
            access_key="demo-key",
            secret_key="demo-secret",
            dry_run=True,
            paper_mode=True,
        )
    return UpbitConfig(
        access_key=access,
        secret_key=secret,
        dry_run=bool(resolved_dry),
        paper_mode=paper,
    )


def validate_upbit_config(cfg: UpbitConfig) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if cfg.paper_mode:
        warnings.append("CRYPTO_PAPER_MODE active — all Upbit orders forced to dry-run.")
    if cfg.dry_run and not cfg.paper_mode:
        warnings.append("UPBIT_DRY_RUN active — no real orders will be sent.")
    if not cfg.dry_run:
        if len(cfg.access_key) < 8:
            errors.append("UPBIT_ACCESS_KEY looks too short.")
        if len(cfg.secret_key) < 8:
            errors.append("UPBIT_SECRET_KEY looks too short.")
    return errors, warnings
