"""Bithumb API credentials from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled, effective_dry_run


class BithumbConfigError(ValueError):
    pass


_MOCK_KEYS = {"dry-run-key", "demo-key"}


@dataclass(frozen=True)
class BithumbConfig:
    api_key: str
    secret_key: str
    dry_run: bool = True
    paper_mode: bool = False

    @property
    def is_demo(self) -> bool:
        return self.api_key in _MOCK_KEYS

    def masked_summary(self) -> dict[str, str | bool]:
        return {
            "api_key": "DEMO (no key)" if self.is_demo else (
                f"{self.api_key[:4]}…{self.api_key[-4:]}" if len(self.api_key) >= 8 else "(set)"
            ),
            "secret_key": "(set)" if self.secret_key and not self.is_demo else "(missing)",
            "dry_run": self.dry_run,
            "paper_mode": self.paper_mode,
        }


def load_bithumb_config_from_env(*, dry_run: bool | None = None) -> BithumbConfig:
    paper = crypto_paper_mode_enabled()
    resolved_dry = effective_dry_run(requested_dry_run=dry_run, env_key="BITHUMB_DRY_RUN")
    api_key = (os.environ.get("BITHUMB_API_KEY") or "").strip()
    secret = (os.environ.get("BITHUMB_SECRET_KEY") or "").strip()
    if not api_key or not secret:
        return BithumbConfig(
            api_key="demo-key",
            secret_key="demo-secret",
            dry_run=True,
            paper_mode=True,
        )
    return BithumbConfig(
        api_key=api_key,
        secret_key=secret,
        dry_run=bool(resolved_dry),
        paper_mode=paper,
    )


def validate_bithumb_config(cfg: BithumbConfig) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if cfg.paper_mode:
        warnings.append("CRYPTO_PAPER_MODE active — Bithumb live orders blocked until CRYPTO_PAPER_MODE=false.")
    if cfg.dry_run and not cfg.paper_mode:
        warnings.append("BITHUMB_DRY_RUN active — no real API calls for private endpoints unless --network.")
    if not cfg.dry_run:
        if len(cfg.api_key) < 8:
            errors.append("BITHUMB_API_KEY looks too short.")
        if len(cfg.secret_key) < 8:
            errors.append("BITHUMB_SECRET_KEY looks too short.")
    return errors, warnings
