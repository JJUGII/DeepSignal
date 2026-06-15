"""crypto_telegram_menu — Telegram 메뉴 키보드·핸들러."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_telegram_flow import CryptoTelegramConfig
from deepsignal.crypto_trading.crypto_telegram_menu import (
    MENU_TEXT_HOLDINGS,
    MENU_TEXT_RECOMMEND,
    MENU_TEXT_RECOMMEND_CRYPTO,
    MENU_TEXT_RUNNER_START,
    MENU_TEXT_RUNNER_STATUS,
    MENU_TEXT_RUNNER_STOP,
    RUNNER_STATE_FILE,
    format_combined_holdings_summary,
    main_menu_reply_keyboard,
    process_crypto_telegram_menu_message,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def _cfg() -> CryptoTelegramConfig:
    return CryptoTelegramConfig(
        output_dir="outputs",
        bot_token="test-token",
        allowed_chat_id="12345",
    )


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_main_menu_keyboard_has_asset_and_recommend_buttons() -> None:
    kb = main_menu_reply_keyboard()
    texts = [btn["text"] for row in kb["keyboard"] for btn in row]
    assert MENU_TEXT_HOLDINGS in texts
    assert MENU_TEXT_RECOMMEND_CRYPTO in texts
    assert MENU_TEXT_RUNNER_STOP in texts
    assert MENU_TEXT_RUNNER_START in texts


def test_any_message_shows_menu(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send(cfg, text=None):
        sent.append(text or "")
        return {"ok": True}

    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.telegram_send_menu_message",
        fake_send,
    )
    upd = {"message": {"text": "안녕", "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(), broker=_br())
    assert out is not None
    assert out.get("action") == "menu"


def test_holdings_button(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        lambda cfg, text=None, keyboard=None: {"ok": True},
    )
    upd = {"message": {"text": MENU_TEXT_HOLDINGS, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(), broker=_br())
    assert out is not None
    assert out.get("action") == "holdings"


def test_recommend_parent_shows_choice(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        lambda cfg, text=None, keyboard=None: {"ok": True},
    )
    upd = {"message": {"text": MENU_TEXT_RECOMMEND, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(), broker=_br())
    assert out is not None
    assert out.get("action") == "recommend_choice"


def test_combined_holdings_summary() -> None:
    lines = format_combined_holdings_summary(
        kis={"cost_krw": 280_000, "value_krw": 292_500, "cash_krw": 219_970},
        crypto={"cost_krw": 20_000, "value_krw": 20_338},
        upbit_krw=130_584,
    )
    text = "\n".join(lines)
    assert "투자금액: 300,000원" in text
    assert "현재 평가: 312,838원" in text
    assert "손익: +12,838원" in text
    assert "총자산(평가+현금)" in text


def test_holdings_message_includes_combined_summary(monkeypatch) -> None:
    captured: list[str] = []

    def fake_send(cfg, text=None, keyboard=None):
        captured.append(text or "")
        return {"ok": True}

    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        fake_send,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.kis_holdings_totals",
        lambda db: {"cost_krw": 280_000, "value_krw": 292_500, "cash_krw": 219_970},
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.format_kis_holdings_telegram",
        lambda db: ["=== 국내주식 (KIS) ===", "  005930"],
    )
    upd = {"message": {"text": MENU_TEXT_HOLDINGS, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(
        upd, cfg=_cfg(), broker=_br(), db_path="data/deepsignal.db"
    )
    assert out is not None
    assert out.get("action") == "holdings"
    assert "전체 요약 (국내주식 + 코인)" in captured[0]


def test_runner_stop_start_and_status(monkeypatch, tmp_path) -> None:
    sent: list[str] = []

    def fake_send(cfg, text=None, keyboard=None):
        sent.append(text or "")
        return {"ok": True}

    monkeypatch.setattr("deepsignal.crypto_trading.telegram.menu._send_menu_text", fake_send)
    cfg = CryptoTelegramConfig(output_dir=str(tmp_path), bot_token="test-token", allowed_chat_id="12345")
    br = _br()

    upd_stop = {"message": {"text": MENU_TEXT_RUNNER_STOP, "chat": {"id": "12345"}}}
    out_stop = process_crypto_telegram_menu_message(upd_stop, cfg=cfg, broker=br)
    assert out_stop is not None
    assert out_stop.get("action") == "runner_stop"
    state_path = tmp_path / RUNNER_STATE_FILE
    assert state_path.is_file()
    assert "\"runner_paused\": true" in state_path.read_text(encoding="utf-8").lower()

    upd_status = {"message": {"text": MENU_TEXT_RUNNER_STATUS, "chat": {"id": "12345"}}}
    out_status = process_crypto_telegram_menu_message(upd_status, cfg=cfg, broker=br)
    assert out_status is not None
    assert out_status.get("action") == "runner_status"
    assert any("PAUSED" in m for m in sent)

    upd_start = {"message": {"text": MENU_TEXT_RUNNER_START, "chat": {"id": "12345"}}}
    out_start = process_crypto_telegram_menu_message(upd_start, cfg=cfg, broker=br)
    assert out_start is not None
    assert out_start.get("action") == "runner_start"
    assert "\"runner_paused\": false" in state_path.read_text(encoding="utf-8").lower()
