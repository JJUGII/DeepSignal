"""Ordered feature names for numpy vectors (legacy 22 + extension 28 = 50)."""

from __future__ import annotations

# Original v1 features — order fixed for backward compatibility.
LEGACY_FEATURE_NAMES: tuple[str, ...] = (
    "ret_1m",
    "ret_3m",
    "ret_5m",
    "ret_15m",
    "ret_1h",
    "alpha_vs_btc_1m",
    "alpha_vs_btc_15m",
    "trend_align_1m_3m_15m",
    "volume_ratio_20",
    "taker_buy_ratio",
    "quote_vol_spike_5m",
    "ob_imbalance",
    "ob_spread_frac",
    "ob_depth_1pct",
    "ob_bid_wall_dist_bps",
    "ob_ask_wall_dist_bps",
    "atr_14_1m_pct",
    "realized_vol_20tick",
    "bb_position_20",
    "btc_trend_1h",
    "alt_quote_vol_sum_log",
    "fear_greed_norm",
)

# Phase 1 extensions (indices 22..49) — 28 features.
EXTENDED_FEATURE_NAMES: tuple[str, ...] = (
    "rsi_slope_3m",
    "vwap_deviation",
    "btc_relative_return_5m",
    "ema_spread_1m_3m",
    "price_accel_1m",
    "high_low_position_1m",
    "range_pct_1m",
    "momentum_sign_5m",
    "volume_ratio_1m",
    "volume_ratio_5m",
    "volume_ratio_15m",
    "volume_acceleration",
    "large_trade_count_1m",
    "quote_vol_zscore_20",
    "ob_imbalance_l1",
    "ob_imbalance_l5",
    "spread_bps",
    "bid_wall_distance",
    "ask_wall_distance",
    "ob_slope_bid",
    "ob_imbalance_1m_mean",
    "spread_1m_mean",
    "wall_bid_price_1m",
    "wall_ask_price_1m",
    "alt_total_volume_ratio",
    "fear_greed_index",
    "funding_rate",
    "market_regime",
    "buy_sell_delta",      # 최근 5개 1m 바의 정규화 CVD (-1..+1)
    # ── GSQS Phase-2 추가 피처 ──────────────────────────────────
    "ema_gap_1m",          # (ema9 - ema21) / close * 100 — 1m 단기 EMA 정렬
    "upper_wick_ratio",    # 상단 꼬리/(high-low) 비율 (0~1) — 세력 털기 감지
    "up_vol_ratio_10",     # 최근 10봉 중 상승봉 거래량 비율 (0~1)
    "btc_ret_5m",          # BTC 절대 5분 수익률 (시장 방향 판단)
    "oi_change_pct",       # 미결제약정 변화율 % (신규 포지션 유입/이탈)
    "bid_ask_depth_ratio", # 1% 이내 bid/(bid+ask) 깊이 비율 (>0.5 = 매수 우세)
    # ── GATS 단타 적합 추가 피처 ──────────────────────────────────────
    "long_short_ratio",        # 글로벌 롱/숏 비율 (균형=1.0, >2.0 롱과열)
    "relative_strength_rank",  # 전체 심볼 5m 모멘텀 순위 (0=최하~1=최상)
    "breakout_score",          # 최근 20봉 최고가 대비 현재가 위치 (%, 양수=돌파)
    "trade_acceleration",      # 현재 1분 체결수 vs 5분 평균 대비 가속도 비율
    "whale_trade_ratio",       # 최대 단일 체결 / 평균 체결 비율 (>10 = 고래 출현)
)

FEATURE_NAMES: tuple[str, ...] = LEGACY_FEATURE_NAMES + EXTENDED_FEATURE_NAMES
LEGACY_FEATURE_COUNT = len(LEGACY_FEATURE_NAMES)
FEATURE_COUNT = len(FEATURE_NAMES)
FEATURE_INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}
