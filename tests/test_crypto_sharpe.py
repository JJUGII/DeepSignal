"""Sharpe helper tests."""

from deepsignal.ml.crypto_sharpe import sharpe_from_returns


def test_sharpe_positive_edge() -> None:
    s = sharpe_from_returns([1.0, 0.5, 1.2, 0.8, 0.6])
    assert s > 0


def test_sharpe_flat() -> None:
    assert sharpe_from_returns([0.0, 0.0, 0.0]) == 0.0
