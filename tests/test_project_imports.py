"""주요 모듈 import 스모크 테스트."""

from __future__ import annotations


def test_import_deepsignal_package() -> None:
    import deepsignal

    assert hasattr(deepsignal, "__version__")


def test_import_domain_modules() -> None:
    import deepsignal.ai.model_pipeline  # noqa: F401
    import deepsignal.analyzer.sentiment.sentiment_analyzer  # noqa: F401
    import deepsignal.analyzer.technical.technical_analyzer  # noqa: F401
    import deepsignal.backtest.backtest_engine  # noqa: F401
    import deepsignal.collector.economic.economic_collector  # noqa: F401
    import deepsignal.collector.market.market_collector  # noqa: F401
    import deepsignal.collector.news.news_item  # noqa: F401
    import deepsignal.collector.news.news_collector  # noqa: F401
    import deepsignal.config.settings  # noqa: F401
    import deepsignal.dashboard.dashboard_data  # noqa: F401
    import deepsignal.live_trading.broker_interface  # noqa: F401
    import deepsignal.paper_trading.paper_trading_engine  # noqa: F401
    import deepsignal.pipelines  # noqa: F401
    import deepsignal.pipelines.daily_pipeline  # noqa: F401
    import deepsignal.portfolio.portfolio_engine  # noqa: F401
    import deepsignal.portfolio.portfolio_models  # noqa: F401
    import deepsignal.notifiers.notification_service  # noqa: F401
    import deepsignal.notifiers.webhook_notifier  # noqa: F401
    import deepsignal.reporting.report_service  # noqa: F401
    import deepsignal.risk.risk_manager  # noqa: F401
    import deepsignal.scoring.macro_scorer  # noqa: F401
    import deepsignal.scoring.signal_scorer  # noqa: F401
    import deepsignal.strategy.base_strategy  # noqa: F401
    import deepsignal.strategy.sample_strategy  # noqa: F401
    import deepsignal.storage.database  # noqa: F401


def test_main_import() -> None:
    import main as main_module

    assert callable(main_module.main)
