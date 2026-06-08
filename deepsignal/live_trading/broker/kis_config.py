"""한국투자증권 KIS Open API 설정 (환경 변수만, 하드코딩 금지)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # broker/ → live_trading/ → deepsignal/ → project root
_ENV_FILE = _PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class KISConfig:
    app_key: str
    app_secret: str
    account_no: str
    account_product_code: str
    hts_id: str | None
    env: str = "paper"

    @property
    def base_url(self) -> str:
        """KIS Open API 호스트 (모의/실전)."""
        e = (self.env or "paper").strip().lower()
        if e == "paper":
            return "https://openapivts.koreainvestment.com:29443"
        if e == "live":
            return "https://openapi.koreainvestment.com:9443"
        raise ValueError(f"unsupported KIS env: {self.env!r}")

    @property
    def is_live(self) -> bool:
        return (self.env or "").strip().lower() == "live"


class KisConfigError(ValueError):
    """KIS 설정 로드·검증 실패."""


def load_kis_config_from_env(*, load_dotenv_file: bool = True) -> KISConfig:
    """`.env` 및 OS 환경에서 KIS 설정을 읽는다."""
    if load_dotenv_file:
        load_dotenv(dotenv_path=_ENV_FILE)

    def req(name: str) -> str:
        v = os.getenv(name)
        if v is None or not str(v).strip():
            raise KisConfigError(f"missing or empty environment variable: {name}")
        return str(v).strip()

    app_key = req("KIS_APP_KEY")
    app_secret = req("KIS_APP_SECRET")
    account_no = req("KIS_ACCOUNT_NO")
    account_product_code = req("KIS_ACCOUNT_PRODUCT_CODE")

    hts_raw = os.getenv("KIS_HTS_ID")
    hts_id = None if hts_raw is None or not str(hts_raw).strip() else str(hts_raw).strip()

    env_raw = os.getenv("KIS_ENV", "paper")
    env = str(env_raw).strip().lower() or "paper"

    return KISConfig(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        account_product_code=account_product_code,
        hts_id=hts_id,
        env=env,
    )


def validate_kis_config(config: KISConfig) -> tuple[list[str], list[str]]:
    """
    설정 검증. (errors, warnings).
    errors 가 비어 있지 않으면 사용 불가.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not (config.app_key or "").strip():
        errors.append("KIS_APP_KEY is empty")
    if not (config.app_secret or "").strip():
        errors.append("KIS_APP_SECRET is empty")
    if not (config.account_no or "").strip():
        errors.append("KIS_ACCOUNT_NO is empty")
    if not (config.account_product_code or "").strip():
        errors.append("KIS_ACCOUNT_PRODUCT_CODE is empty")

    env = (config.env or "").strip().lower()
    if env not in ("paper", "live"):
        errors.append(f"KIS_ENV must be 'paper' or 'live', got {config.env!r}")
    else:
        try:
            _ = config.base_url
        except ValueError as e:
            errors.append(str(e))

    cano = (config.account_no or "").strip()
    if cano and not re.fullmatch(r"\d{8}", cano):
        warnings.append(
            "KIS_ACCOUNT_NO is usually 8 digits (CANO). "
            "Verify against KIS Developers / account opening documents."
        )

    prod = (config.account_product_code or "").strip()
    if prod and not re.fullmatch(r"\d{2}", prod):
        warnings.append(
            "KIS_ACCOUNT_PRODUCT_CODE is usually 2 digits (ACNT_PRDT_CD). "
            "Verify against KIS documentation."
        )

    if env == "live":
        warnings.append(
            "KIS_ENV=live: production API hosts. "
            "order-cash POST runs only after live-approve guards "
            "(--broker kis --approved --execute --allow-live-env --final-confirm; see README)."
        )

    return errors, warnings
