"""설정 관리 — .env 읽기/쓰기 (섹션별 구조화)."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

# 화면에 노출할 설정 정의 (key, label, type, section, description)
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    # ── 🔒 개인정보 (KIS / Telegram / Webhook) ───────────────────
    # 코인 API 키는 「코인 (거래소)」 탭에서 거래소 선택 시 표시
    {"key": "KIS_APP_KEY",         "label": "KIS App Key",       "type": "secret",  "section": "private", "group": "KIS API",      "desc": "한국투자증권 API App Key"},
    {"key": "KIS_APP_SECRET",      "label": "KIS App Secret",    "type": "secret",  "section": "private", "group": "KIS API",      "desc": "한국투자증권 API App Secret"},
    {"key": "KIS_ACCOUNT_NO",      "label": "KIS 계좌번호",       "type": "secret",  "section": "private", "group": "KIS API",      "desc": "CANO 8자리 (예: 12345678)"},
    {"key": "KIS_HTS_ID",          "label": "KIS HTS ID",        "type": "secret",  "section": "private", "group": "KIS API",      "desc": "HTS 로그인 ID"},
    # Telegram
    {"key": "TELEGRAM_BOT_TOKEN",  "label": "Telegram Bot Token","type": "secret",  "section": "private", "group": "Telegram",     "desc": "BotFather에서 발급한 봇 토큰"},
    {"key": "TELEGRAM_CHAT_ID",    "label": "Telegram Chat ID",  "type": "secret",  "section": "private", "group": "Telegram",     "desc": "알림 수신 채팅 ID"},
    {"key": "DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "label": "알림 전용 Chat ID", "type": "secret", "section": "private", "group": "Telegram", "desc": "별도 알림 채팅 ID (비우면 위와 동일)"},
    # Webhook
    {"key": "WEBHOOK_URL",         "label": "Webhook URL",       "type": "secret",  "section": "private", "group": "알림 서비스", "desc": "Discord / Slack 웹훅 URL"},

    # ── 코인 (거래소) — CRYPTO_BROKER 선택에 따라 Upbit/Bithumb 패널 전환 ──
    {"key": "CRYPTO_BROKER",       "label": "활성 거래소 (차트·주문)", "type": "select",  "section": "upbit", "exchange": "common",
     "desc": "차트·호가·Web 승인·자동매매 러너에 사용할 거래소입니다. 대시보드는 Upbit·Bithumb 잔고를 항상 함께 표시합니다.",
     "options": ["upbit", "bithumb"]},
    {"key": "UPBIT_ACCESS_KEY",    "label": "Access Key",        "type": "secret",  "section": "upbit", "exchange": "upbit",
     "desc": "Upbit Open API Access Key (https://upbit.com/mypage/open_api_management)"},
    {"key": "UPBIT_SECRET_KEY",    "label": "Secret Key",        "type": "secret",  "section": "upbit", "exchange": "upbit",
     "desc": "Upbit Open API Secret Key"},
    {"key": "UPBIT_DRY_RUN",       "label": "Dry Run",           "type": "bool",    "section": "upbit", "exchange": "upbit",
     "desc": "ON이면 Upbit 실주문을 보내지 않습니다. OFF + 페이퍼 모드 OFF 시 실거래 가능."},
    {"key": "BITHUMB_API_KEY",     "label": "API Key",           "type": "secret",  "section": "upbit", "exchange": "bithumb",
     "desc": "Bithumb Open API 2.x API Key"},
    {"key": "BITHUMB_SECRET_KEY",  "label": "Secret Key",        "type": "secret",  "section": "upbit", "exchange": "bithumb",
     "desc": "Bithumb Open API 2.x Secret Key"},
    {"key": "BITHUMB_DRY_RUN",     "label": "Dry Run",           "type": "bool",    "section": "upbit", "exchange": "bithumb",
     "desc": "ON이면 Bithumb 실주문을 보내지 않습니다. OFF + 페이퍼 모드 OFF 시 실거래 가능."},
    {"key": "CRYPTO_PAPER_MODE",   "label": "페이퍼 모드",        "type": "bool",    "section": "upbit", "exchange": "common",
     "desc": "가상 잔고로 매매를 시뮬레이션합니다. 첫 실행 후 14일이 지나야 실거래로 전환되는 안전장치입니다."},
    {"key": "CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL", "label": "무승인 자동실행(매수+매도)", "type": "bool", "section": "upbit", "exchange": "common",
     "desc": "STRONG 신호 발생 즉시 텔레그램 승인 없이 자동 매수·매도를 실행합니다. 매수(GSQS 임계값 초과)와 ATR 기반 TP/SL 매도가 모두 자동으로 처리됩니다."},
    {"key": "CRYPTO_GATE_MODE",    "label": "Gate 모드",          "type": "select",  "section": "upbit", "exchange": "upbit",
     "desc": "매수 진입 판단 방식을 선택합니다. hybrid=전통 지표+ML 혼합(권장), ml_primary=ML 결과 우선, ml_only=ML 신호만 사용",
     "options": ["hybrid", "ml_primary", "ml_only"]},
    {"key": "CRYPTO_ENSEMBLE_MODE","label": "Ensemble 모드",      "type": "select",  "section": "upbit", "exchange": "upbit",
     "desc": "여러 ML 모델의 신호를 합치는 방식입니다. unanimous=전 모델 동의 시만 매수(보수적), weighted=가중 평균(균형), lgbm_only=LGBM 모델만 사용",
     "options": ["unanimous", "weighted", "lgbm_only"]},
    {"key": "CRYPTO_ML_BUY_GATE",  "label": "ML 매수 게이트",    "type": "bool",    "section": "upbit", "exchange": "upbit",
     "desc": "머신러닝 모델의 예측 승률이 55% 이상일 때만 매수를 허용하는 필터입니다. 불확실한 구간 진입을 차단합니다."},
    {"key": "CRYPTO_ML_ENSEMBLE",  "label": "ML 앙상블",          "type": "bool",    "section": "upbit", "exchange": "upbit",
     "desc": "LGBM(의사결정 트리 계열), LSTM(시계열 딥러닝), 규칙 기반 3가지 모델을 동시에 실행해 합산한 결과로 최종 신호를 생성합니다."},
    {"key": "CRYPTO_DYNAMIC_SPREAD_ENABLED", "label": "스프레드 게이트", "type": "bool", "section": "upbit", "exchange": "upbit",
     "desc": "매수 직전 호가창(오더북) 스프레드를 실시간 측정해 스프레드가 넓을 때는 진입을 차단합니다. 슬리피지를 줄이는 품질 필터입니다."},
    {"key": "CRYPTO_SPREAD_HARD_MAX_PCT", "label": "최대 허용 스프레드 (%)", "type": "text", "section": "upbit", "exchange": "upbit",
     "desc": "이 값(%)을 초과하는 스프레드가 감지되면 무조건 매수를 차단합니다. 기본값 0.80. 값이 낮을수록 보수적입니다."},
    {"key": "CRYPTO_DYNAMIC_TP_SL_ENABLED", "label": "동적 TP/SL", "type": "bool", "section": "upbit", "exchange": "upbit",
     "desc": "시장 상황(강세/약세)에 따라 익절·손절 기준을 ATR 배수로 자동 조정합니다. 꺼두면 고정 TP/SL 비율을 사용합니다."},
    {"key": "SCALP_SIGNAL_COOLDOWN_MINUTES", "label": "신호 쿨다운 (분)", "type": "text", "section": "upbit", "exchange": "upbit",
     "desc": "같은 심볼에서 신호가 연속 발생할 때 알림 전송을 억제하는 시간(분)입니다. 기본값 15. 0으로 설정하면 쿨다운 없음."},
    {"key": "SCALP_SIGNAL_MAX_PER_HOUR", "label": "시간당 최대 신호 수", "type": "text", "section": "upbit", "exchange": "upbit",
     "desc": "1시간 내 Telegram으로 전송할 수 있는 최대 신호 개수입니다. 기본값 20. 알림 과부하 방지용 안전장치입니다."},

    # ── KIS 매매 설정 ──────────────────────────────────────────────
    {"key": "KIS_ENV",             "label": "KIS 실전 거래 모드", "type": "env_bool", "section": "kis",
     "value_true": "live", "value_false": "paper",
     "desc": "OFF = 모의투자(한국투자증권 테스트 서버, 실제 돈 없음). ON = 실전(실제 계좌, 실제 돈). 반드시 확인 후 활성화하세요."},
    {"key": "_KIS_FULL_AUTO",      "label": "무승인 자동실행(매수+매도)", "type": "multi_bool", "section": "kis",
     "keys": ["KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", "KIS_AUTO_SELL_TAKE_PROFIT", "KIS_AUTO_SELL_STOP_LOSS"],
     "desc": "국내·해외주식 매수(AI 플랜 즉시 실행)와 매도(ATR 익절·손절)를 모두 자동화합니다. 개별 제어가 필요하면 직접 .env를 편집하세요."},
    {"key": "KIS_STOCK_MAX_ORDER_VALUE", "label": "1회 최대 주문금액 (원)", "type": "text", "section": "kis",
     "desc": "단일 주문으로 집행할 수 있는 최대 금액(원)입니다. 기본값 300,000원. 이 한도를 초과하는 플랜은 자동으로 차단됩니다."},
    {"key": "KIS_STOCK_MAX_ORDERS_PER_DAY", "label": "일일 최대 주문 수", "type": "text", "section": "kis",
     "desc": "하루에 실행할 수 있는 최대 주문 건수입니다. 기본값 3건. 과도한 거래를 방지하는 위험 관리 한도입니다."},
    {"key": "KIS_STOCK_EXTRA_SYMBOLS", "label": "추가 스캔 심볼", "type": "text", "section": "kis",
     "desc": "AI 스캔 유니버스에 항상 포함할 종목 코드를 쉼표로 구분해 입력합니다. 예: 005930,NVDA,TSLA. 국내·해외 혼합 가능합니다."},
    {"key": "OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL", "label": "무승인 자동실행(매수+매도)", "type": "bool", "section": "kis",
     "desc": "미국 정규장(22:30~05:00 KST)에 K-GSQS 신호 발생 시 승인 없이 자동 매수·매도(ATR TP/SL)를 실행합니다. 코인·국내주식과 동일하게 '전체 일시정지' 토글로 즉시 중단할 수 있습니다. 원화 통합증거금 자동 환전."},
    {"key": "OVERSEAS_CAPITAL_USD", "label": "해외 매수 자본 (USD)", "type": "text", "section": "kis",
     "desc": "해외주식 매수에 사용할 총 자본(USD)입니다. 기본값 1000. 이 자본 내에서 종목별로 분산 매수합니다."},
    {"key": "OVERSEAS_MAX_SINGLE_ORDER_USD", "label": "해외 1회 최대 주문 (USD)", "type": "text", "section": "kis",
     "desc": "해외주식 단일 주문의 USD 상한입니다. 기본값 300. 이 한도를 초과하면 수량을 자동 축소합니다."},

    # ── Telegram 표시 설정 ─────────────────────────────────────────
    {"key": "TELEGRAM_MENU_VERBOSE_LOG", "label": "상세 로그",   "type": "bool",    "section": "telegram",
     "desc": "텔레그램 봇 메뉴에서 서버 상태를 조회할 때 간략 요약 대신 상세 로그까지 함께 출력합니다. 기본 꺼짐."},

    # ── 시스템 ─────────────────────────────────────────────────────
    {"key": "DEEPSIGNAL_ALLOW_AFTER_HOURS",     "label": "시간외 거래",     "type": "bool", "section": "system",
     "desc": "주말·공휴일·장 마감 이후에도 거래를 허용합니다. 24시간 운영하는 코인에는 영향이 없으며 주식 종목에 적용됩니다."},
    {"key": "NOTIFY_ON_FAILURE",   "label": "실패 알림",          "type": "bool",    "section": "system",
     "desc": "run-daily 스케줄 작업이 오류로 실패했을 때 등록된 Webhook(Discord/Slack)으로 즉시 알림을 보냅니다."},
    {"key": "MARKET_PERIOD",       "label": "데이터 기간",         "type": "text",    "section": "system",
     "desc": "yfinance로 주가 데이터를 가져올 기간입니다. 예: 6mo=6개월, 1y=1년. 길수록 분석이 정확하지만 처리 시간이 늘어납니다."},
]

SECTION_LABELS = {
    "private":  "🔒 개인정보",
    "upbit":    "코인 (거래소)",
    "kis":      "KIS (주식)",
    "telegram": "Telegram",
    "system":   "시스템",
}

# 개인정보 탭 안의 그룹 순서
PRIVATE_GROUPS = ["KIS API", "Telegram", "알림 서비스"]

_SECRET_MASK = "••••••••"
_SHOW_LAST = 4  # 시크릿 마지막 N자 표시


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= _SHOW_LAST:
        return _SECRET_MASK
    return _SECRET_MASK + value[-_SHOW_LAST:]


def read_env_raw(env_path: Path) -> dict[str, str]:
    """모든 KEY=VALUE 파싱 (주석/빈줄 제외)."""
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _is_truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on", "live")


def read_settings(env_path: Path) -> dict[str, Any]:
    """UI에 전달할 설정 구조 (시크릿 마스킹 포함)."""
    raw = read_env_raw(env_path)
    sections: dict[str, list[dict[str, Any]]] = {s: [] for s in SECTION_LABELS}

    for item in SETTINGS_SCHEMA:
        key = item["key"]
        typ = item["type"]

        if typ == "env_bool":
            raw_val = raw.get(key, "")
            display_val = "true" if raw_val == item.get("value_true", "true") else "false"
            entry: dict[str, Any] = {
                "key": key, "label": item["label"], "type": "bool",
                "desc": item.get("desc", ""), "value": display_val, "set": bool(raw_val),
            }
        elif typ == "multi_bool":
            keys = item.get("keys", [])
            all_on = bool(keys) and all(_is_truthy(raw.get(k, "")) for k in keys)
            entry = {
                "key": key, "label": item["label"], "type": "bool",
                "desc": item.get("desc", ""), "value": "true" if all_on else "false", "set": all_on,
            }
        else:
            val = raw.get(key, "")
            is_secret = typ == "secret"
            entry = {
                "key":   key,
                "label": item["label"],
                "type":  typ,
                "desc":  item.get("desc", ""),
                "value": _mask(val) if is_secret else val,
                "set":   bool(val),
            }
            if "options" in item:
                entry["options"] = item["options"]
        if "exchange" in item:
            entry["exchange"] = item["exchange"]
        if "group" in item:
            entry["group"] = item["group"]
        sections[item["section"]].append(entry)

    return {
        "sections": sections,
        "section_labels": SECTION_LABELS,
    }


def write_settings(env_path: Path, updates: dict[str, str]) -> tuple[bool, str]:
    """
    updates: {KEY: new_value} — 마스킹 값은 원본 유지.
    .env.bak 백업 후 저장.
    """
    if not env_path.is_file():
        env_path.touch()

    # 백업
    bak = env_path.with_suffix(".bak")
    try:
        shutil.copy2(env_path, bak)
    except Exception:
        pass

    raw = read_env_raw(env_path)

    # 마스킹 값은 무시 (변경 없음)
    schema_map = {s["key"]: s for s in SETTINGS_SCHEMA}
    for key, val in updates.items():
        schema = schema_map.get(key, {})
        typ = schema.get("type", "text")

        if typ == "secret":
            if val.startswith(_SECRET_MASK) or val == "":
                if val == "":
                    raw.pop(key, None)
                continue
            raw[key] = val
        elif typ == "env_bool":
            is_on = val.lower() in ("true", "1", "yes", "on")
            raw[key] = schema.get("value_true", "live") if is_on else schema.get("value_false", "paper")
        elif typ == "multi_bool":
            is_on = val.lower() in ("true", "1", "yes", "on")
            str_val = "true" if is_on else "false"
            for mk in schema.get("keys", []):
                raw[mk] = str_val
        else:
            raw[key] = val

    # 파일 재작성 (기존 주석 보존하면서 값만 업데이트)
    try:
        original = env_path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        written: set[str] = set()
        new_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                new_lines.append(line)
                continue
            if "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in raw:
                    new_lines.append(f"{k}={raw[k]}\n")
                    written.add(k)
                elif k in updates and updates[k] == "":
                    # 삭제 요청 → 줄 생략
                    pass
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # 새로 추가된 키 (기존에 없던 것)
        new_keys = [k for k in raw if k not in written]
        if new_keys:
            new_lines.append("\n# Web UI 추가\n")
            for k in new_keys:
                if k in updates:  # 업데이트로 추가된 것만
                    new_lines.append(f"{k}={raw[k]}\n")

        env_path.write_text("".join(new_lines), encoding="utf-8")
        return True, f"{len(updates)}개 설정 저장 완료 (백업: {bak.name})"
    except Exception as e:
        return False, str(e)
