"""경량 OpenAI 호환 LLM 클라이언트 (requests 기반).

핫패스에서 직접 쓰지 않는다. 키가 없거나 호출이 실패하면 None을 반환해
상위 로직이 fail-open(중립 처리)하도록 한다. 모델은 env로 교체 가능
(DEEPSIGNAL_LLM_MODEL — 예: gpt-4o-mini, gpt-5.5 등).
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_BASE = "https://api.openai.com/v1"


class LLMClient:
    def __init__(self, api_key: str, *, model: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("DEEPSIGNAL_LLM_MODEL", _DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("DEEPSIGNAL_LLM_BASE_URL", _DEFAULT_BASE)).rstrip("/")

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 700,
        temperature: float = 0.0,
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        """JSON 응답을 강제하고 dict로 반환. 실패 시 None."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=timeout)
            if resp.status_code != 200:
                return None
            body = resp.json()
            content = (
                body.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            if not content:
                return None
            return json.loads(content)
        except Exception:
            return None


def get_llm_client() -> LLMClient | None:
    """env에서 OpenAI 키를 읽어 클라이언트 생성. 키 없으면 None(=LLM 비활성)."""
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key or not key.startswith("sk-"):
        return None
    return LLMClient(key)


def llm_news_enabled() -> bool:
    """LLM 뉴스 감성 기능 on/off. 기본 OFF — 명시적으로 켜야 동작."""
    raw = os.environ.get("CRYPTO_LLM_NEWS_ENABLED", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")
