"""CRYPTO_PAPER_MODE — force dry-run orders; live only when explicitly disabled."""

from __future__ import annotations

import os


class CryptoPaperModeError(RuntimeError):
    """Raised when live Upbit order execution is blocked by paper mode."""


def crypto_paper_mode_enabled() -> bool:
    """True unless CRYPTO_PAPER_MODE is explicitly false/off/0."""
    raw = (
        os.environ.get("CRYPTO_PAPER_MODE")
        or os.environ.get("DEEPSIGNAL_CRYPTO_PAPER_MODE")
        or "true"
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def effective_dry_run(*, requested_dry_run: bool | None = None, env_key: str = "UPBIT_DRY_RUN") -> bool:
    """Paper mode always forces dry_run. Otherwise honor requested or env_key (default UPBIT_DRY_RUN)."""
    if crypto_paper_mode_enabled():
        return True
    if requested_dry_run is not None:
        return bool(requested_dry_run)
    flag = (os.environ.get(env_key) or "true").strip().lower()
    return flag not in ("0", "false", "no", "off")


def require_live_trading_allowed(*, context: str = "") -> None:
    """Hard guard: live POST /orders only when CRYPTO_PAPER_MODE=false."""
    if crypto_paper_mode_enabled():
        suffix = f" ({context})" if context else ""
        raise CryptoPaperModeError(
            "CRYPTO_PAPER_MODE is active — real Upbit orders are blocked. "
            "Set CRYPTO_PAPER_MODE=false in .env to allow live trading"
            f"{suffix}."
        )


def orders_blocked_by_paper_or_dry_run(*, dry_run: bool, paper_mode: bool, execute: bool) -> bool:
    """Shared predicate for buy/sell/cancel paths."""
    return bool(paper_mode) or bool(dry_run) or not execute
