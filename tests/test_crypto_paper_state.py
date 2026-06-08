"""Paper period counter."""

from __future__ import annotations

import json

import pytest

from deepsignal.crypto_trading.crypto_paper_state import (
    load_paper_state,
    paper_state_path,
    touch_paper_state,
)


@pytest.fixture(autouse=True)
def _paper_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "true")


def test_touch_paper_state_creates_file(tmp_path) -> None:
    out = tmp_path
    st = touch_paper_state(out)
    assert st is not None
    assert st.elapsed_days >= 1
    path = paper_state_path(out)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["unlock_condition"] == "elapsed_days >= 14"
    assert data["required_days"] == 14


def test_touch_increments_once_per_day(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path
    st1 = touch_paper_state(out)
    assert st1 is not None
    st2 = touch_paper_state(out)
    assert st2 is not None
    assert st2.elapsed_days == st1.elapsed_days

    loaded = load_paper_state(out)
    assert loaded is not None
    loaded.last_tick_date = "1999-01-01"
    from deepsignal.crypto_trading.crypto_paper_state import save_paper_state

    save_paper_state(out, loaded)
    st3 = touch_paper_state(out)
    assert st3 is not None
    assert st3.elapsed_days == st1.elapsed_days + 1
