# DeepSignal — 현재 상태

최종 갱신: **로그인 후 launchd 건강검사 자동화**

## 완료 (재부팅 후 프로세스 점검)

- `launchd-health-check` — 코인/KIS/Binance 3개 LaunchAgent `running` 확인, JJU·venv·`.env` 점검
- `install-launchd-health-check` → `com.deepsignal.launchd_health_check` (로그인 후 ~90초, 1회)
- 실패 시 `kickstart` + Telegram 요약
- 재부팅(로그인) 후: `getMe` 봇 API 점검 + 요약 메시지 + 메인 메뉴 키보드 (`LAUNCHD_HEALTH_TELEGRAM_BOOT_OK` 기본 true)
- 상태: `launchd-health-check-status` / 마지막 결과 `~/.deepsignal/launchd_health_last.json`

최종 갱신(이전): **Telegram 메뉴 메시지 간소화**

## 완료 (Telegram UX)

- 메뉴 응답: 파일경로·bps·게이트 디버그 제거 → `telegram_user_format.py` (추천/무추천/자산 요약)
- 메뉴 스캔 중 알림 기본 **OFF** (`TELEGRAM_MENU_SCAN_PROGRESS=false`)
- 서버 `[menu]` 로그 기본 **OFF** (오류만 출력, `TELEGRAM_MENU_VERBOSE_LOG=true` 시 전체)

최종 갱신(이전): **코인 Telegram 승인 vs 러너 자동체결 분리**

## 완료 (코인 승인/자동 분리)

- Telegram **「현재 추천 보기 — 코인」** → 항상 **승인/거부** (`should_skip_crypto_telegram_approval(from_telegram_menu=True)` → False)
- **crypto_auto_runner tick** (`CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL` 또는 비활성 시간대) → 승인 없이 `execute_crypto_plan_inactive_auto`
- 추천 없을 때 저장된 `CRYPTO_ORDER_PLAN.json` 있으면 메뉴에서 승인 버튼 **재전송** (`resend_crypto_approval_from_saved_plan`)
- `crypto-telegram-approval` CLI는 수동 경로 — env 자동체결 분기 제거
- launchd `com.deepsignal.crypto_auto_runner` 재설치·kickstart (execute=True, poll=False)

**참고**: tick에서도 `build_daily_crypto_recommendation`이 None이면 거래 없음 (R:R·스프레드 게이트). 진단 메시지의 “27건 탈락”과 동일.

최종 갱신(이전): **KIS 국내주식 장중 자동매매 + 종목 스캔 확대**

## 완료 (KIS 장중 자동매매)

- `KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL=true` — **09:00~15:30** 정규장 승인 없이 plan 실행, Telegram은 **매수·매도 체결 결과만** (`kis_stock_auto_execute_policy.py`, `daily_ai_auto_runner.py`)
- orders=0 일 때 Telegram 미발송 (승인 모드에서만 "오늘 주문 없음")
- 종목 스캔: `KIS_STOCK_SIGNAL_SCAN_LIMIT`, `KIS_STOCK_INCLUDE_MARKET_UNIVERSE` + `market_prices` DISTINCT, `KIS_STOCK_MAX_RECOMMENDATIONS`
- `.env` 한도: `KIS_STOCK_MAX_ORDERS_PER_DAY` 등 → `daily-ai-auto-runner` / launchd

최종 갱신(이전): **[ML Phase 4] crypto_trades 피드백 DB + 재학습 루프**

## 완료 ([ML Phase 4] 피드백·재학습)

- `outputs/crypto_trades.db` — `crypto_trades` 테이블 (진입 피처·확률·gate_mode·paper).
- 매수/매도 체결 시 `apply_crypto_fill_update` → INSERT/UPDATE (스냅샷 실패해도 체결 유지).
- `crypto-retrain-lgbm` — 최근 14일 체결 기반 레이블, warm-start(기본), `--also-seq`, 배포 게이트(auc≥0.52, val_sharpe≥0.5×train, trades≥30).
- `outputs/retrain_history.jsonl` + CLI `crypto-retrain-history`.
- launchd 재학습에 `--also-seq` 포함.

최종 갱신(이전): **[ML Phase 3] 게이트 모드·앙상블·동적 청산 fallback·임계값 제안**

## 완료 ([ML Phase 3] AI 게이트 모드)

- `CRYPTO_GATE_MODE` — `hybrid`(score≥45 & ml≥0.50) | `ml_primary`(ml≥threshold, score는 랭킹만) | `ml_only`(실험, val_sharpe≥0.5 확인 후).
- `CRYPTO_ENSEMBLE_MODE` — `unanimous` | `weighted`(0.5/0.3/0.2) | `lgbm_only` (LSTM bars 부족 시 자동 fallback).
- 동적 청산 우선: AI P<0.4 → 트레일 -0.8% → 5분 시간손절 → +1.2% 50% 부분익절; `CRYPTO_SELL_FALLBACK_ONLY` 시 near_tp/sl·고정 TP/SL만 fallback.
- CLI: `crypto-ml-suggest-config` → `outputs/CRYPTO_ML_ENV_SUGGESTION.md` (자동 .env 적용 없음).
- 로그: `[GATE] mode=ml_primary prob=0.61 score=52`

최종 갱신(이전): **[ML Phase 2] crypto-validate-ml 검증·과적합·임계값 스윕**

## 완료 ([ML Phase 2] 검증 시스템)

- `crypto-validate-ml` — `replay_at` 피처, fee×2 레이블, TimeSeriesSplit(gap=10), 슬리피지 ask+0.5×spread.
- 산출: `outputs/CRYPTO_ML_VALIDATION_REPORT.md`, `CRYPTO_ML_THRESHOLD_REPORT.md`, `crypto_ml_validation_latest.json`.
- P×N 스윕: P∈{0.50..0.60}, N∈{3,5,10} — Sharpe 최대 조합 권장.
- `tests/test_crypto_no_lookahead.py` — 미래 봉/호가 누수 검증.

최종 갱신(이전): **[데이터 Phase 1] 호가 히스토리 + 피처 50 + 리플레이 + Fear&Greed**

## 완료 ([데이터 Phase 1] 피처·호가·리플레이)

- `bars/{SYMBOL}_ob.jsonl` — depth 10초 스냅샷 (`ob_snapshot_seconds` 기본 10).
- 1분 OB 집계: `ob_imbalance_1m_mean`, `spread_1m_mean`, `wall_bid/ask_price_1m`.
- **FeatureEngine 50차원** — 기존 22개 순서 유지 + 확장 28개 (`spec.LEGACY_FEATURE_NAMES`).
- `FeatureEngine.replay_at(symbol, ts_ms)` — bars+ob jsonl, **ts 이전만** (look-ahead 금지).
- `tests/test_no_lookahead.py` — 미래 봉/호가 미포함 검증.
- `fetch-fear-greed` / `outputs/fear_greed_cache.json` — Alternative.me F&G 일 1회.
- CLI: `fetch-fear-greed`, `binance-stream --ob-snapshot-seconds 10`.

## 완료 ([코인 Phase 0] CRYPTO_PAPER_MODE 페이퍼 안전장치)

## 완료 ([코인 Phase 0] 페이퍼 트레이딩 안전장치)

- `CRYPTO_PAPER_MODE=true`(기본) — Upbit buy/sell/cancel 강제 dry-run, `--execute` CLI 차단.
- `outputs/CRYPTO_PAPER_STATE.json` — `elapsed_days` / 14일 unlock, 러너 tick마다 갱신.
- CLI: `python main.py crypto-paper-status`
- outcomes DB `paper` 컬럼 — 페이퍼·실전 분리.
- `live_state` 3분(기본) stale 시 Telegram: `⚠️ [DeepSignal] BTC 스트림 stale …`
- 러너 루프 5초마다 신선도·페이퍼 카운터 체크 (`menu_poll_seconds` 기본 5).

최종 갱신(이전): **[데이터] Binance WebSocket 실시간 파이프라인 (tick·호가·OHLCV)**

## 완료 ([ML] 2~3단계 + 운영 스케줄)

- **LSTM/Transformer**: `crypto-train-seq --model lstm|transformer` (PyTorch, seq=30, early stop).
- **앙상블**: LGBM + 시퀀스 + Rule 점수 **모두 ≥ 0.55** (`CRYPTO_ML_ENSEMBLE=true`).
- **live_state 스캔**: `CRYPTO_USE_LIVE_STATE_SCAN` — 신선할 때 Upbit `/market/all` 생략.
- **재학습 launchd**: `install-crypto-retrain-launchd` (기본 03:10).
- **Sharpe 배포**: `crypto-retrain-lgbm` — AUC + 실현거래 Sharpe(14일) 동시 검증.

## 완료 ([ML] BUY 게이트 + 피드백·재학습 v1)

- `crypto_ml_gate.py` — P(win)≥0.55 BUY 필터 (`CRYPTO_ML_BUY_GATE`, strict 옵션).
- `crypto_recommendation_outcomes` — `model_probability`, `features_snapshot_json`, `entry_time`.
- `crypto-retrain-lgbm` — 검증 AUC ≥ baseline 시만 `crypto_scalp_lgbm_active.json` 배포, 미만 시 backup 롤백.
- `install-binance-stream-launchd` — WS 상시 수집 LaunchAgent.
- 러너 tick에 `binance_live_state` 신선도 기록.

## 완료 ([코인] 실행층 Execution Engine v1)

- `crypto_execution_engine.py` — P(win)>0.55 매수, 호가 스프레드/매수벽, mid·bid+1틱 지정가, **10초 타임아웃·1회 재시도**, Kelly(최대 5%), 동적 청산.
- 매도: AI P(win)<0.4, 트레일링 -0.8%, 5분 시간손절, +1.2% **50% 부분익절**.
- `CRYPTO_EXECUTION_ENGINE=false` 시 기존 `place_limit_buy_with_requote` 경로.
- Upbit `get_orderbook` 추가.

## 완료 ([ML] LightGBM 단타 P(win) 1단계)

- 레이블: **y=1** if N분 후 수익률 > **0.2%** (수수료+슬리피지), else 0.
- `crypto-train-lgbm` — FeatureEngine 피처 + TimeSeriesSplit + 피처 중요도.
- `crypto-predict-lgbm` — P(win) 0~1, 임계값 기본 **0.55**.
- 모델: `outputs/models/crypto_scalp_lgbm_{N}m.txt`

## 완료 ([데이터] FeatureEngine v1)

- `deepsignal/market_data/feature_engine/` — 가격·거래량·호가·변동성·시장국면 **22차원** numpy 벡터, **forward-fill**.
- CLI: `python main.py binance-features` (`live_state.json` 기반).
- `binance-stream` 종료 시 `feature_vectors.json` 스냅샷 (선택).

## 완료 ([데이터] Binance WebSocket v1)

- `deepsignal/market_data/binance_stream/` — 상위 30 USDT(또는 `--symbols`) tick·depth20·펀딩(markPrice)·BTC 참조.
- **1m / 3m / 15m** 봉 tick 집계 → `outputs/binance_stream/bars/{SYMBOL}_{tf}.jsonl`.
- 스냅샷 `live_state.json` (호가·미완성 봉·최근 체결·펀딩).
- CLI: `python main.py binance-stream` (`--duration 0` = 상시).

## 완료 ([코인] 익절 +2% 지정가 매도)

## 완료 ([코인] 매도 가격·집중도)

- 익절/근접 익절: **평단 × (1 + TP%)** 지정가 (단타 +2%).
- 손절/근접 손절: **평단 × (1 + SL%)** 지정가.
- **1종목만 보유** 시 과집중(100%)으로 현재가 매도하지 않음 — 다종목·PnL≥1.2%만 `overweight_reduce`.
- `_persist_trade_state` — 매수/매도 후 쿨다운·`position_open_ts`·일일 카운터 저장.

## 완료 ([코인] 단타 모드 복구)

## 완료 ([코인] 단타 모드 복구)

- `scalping_mode=true` — outcome 튜닝이 TP 10%·vol 0.85로 덮어쓰지 않음. **ATR도 TP/SL에 미적용** (고정 +2% / −1.5%).
- 진단 메시지에 **체결품질(R:R·스프레드) 탈락** 사유 표시, `portfolio_total` 반영.
- `min_final_score` **45** 고정 (주식 60점 문턱 미적용).
- `CRYPTO_ACTIVE_THRESHOLDS.json` 단타 기본값 리셋 API `reset_scalping_active_thresholds`.
- `outcome_tune_max_volume_ratio` **0.45** 상한.

## 완료 ([코인] P0~P2 과매매·집중도·보유시간 가드)

## 완료 ([코인] P0~P2 과매매·포지션 가드)

- `crypto_overtrading_guards.py` — 재매수 쿨다운 **20분**, 시간당 BUY **2회/종목**, SELL 후 **15분** 재진입 금지, 종목당 일일 BUY **총자산 12%** cap, **3회/일** 추가매수 cap.
- 최소 보유 **5분**(SL/TP 제외), `near_take_profit`은 **+1.2%** 이상만, 집중도 block **18%**, 단일 주문 **총자산 8%** cap.
- P4(부분): 세션 필터(24h 변동·거래대금), 체결 슬리피지 `CRYPTO_FILL_SLIPPAGE.jsonl` 피드백.

## 완료 ([코인] 체결·주문 품질 엔진)

- `crypto_execution_quality.py` — 최소주문 **10,000원** 강제, 스프레드 추정, 수수료·슬리피지 반영 R:R·순이익 목표 검사.
- BUY 추천·러너·실주문 전 이중 게이트; 미통과 시 다음 후보 종목 시도(최대 12개).
- 지정가 **재호가**(최대 2회, +0.05% tick) + 미체결 취소 — 시장가 주문 없음.
- `upbit_broker` 정책 최소주문·`cancel_order` 추가.

## 완료 ([코인] Telegram 러너 제어 버튼)

- `crypto_telegram_menu.py` 메인 키보드에 `러너 정지` / `러너 시작` / `러너 상태` 버튼을 추가했다.
- 버튼 클릭 시 `outputs/CRYPTO_AUTO_RUNNER_STATE.json`의 `runner_paused` 플래그를 갱신한다.
- `crypto_auto_runner.py` 루프는 `runner_paused=true`면 분석 tick을 건너뛰고 메뉴/콜백만 계속 처리한다.
- 따라서 Telegram에서 즉시 정지/재개가 가능하고, launchd 프로세스는 유지된다.

## 완료 ([코인] 단타 모드 기본값 전환)

- `analysis_conditions.crypto` — TP **+2.0%**, SL **−1.5%**, 경고 **−0.8%/+1.2%**, 진입리뷰 **−2.5%**.
- 포지션/주문: 가용 KRW 기준 주문 비율 상향(단타용), 단일종목 상한 **15%**, 과집중 warn/block **10%/15%**.
- 스캔/신호: `min_final_score=55`, `technical_weight=0.8`, `macro_weight=0.2`, `max_buy_scan_markets=100`.
- 실행 주기: auto-runner/launchd 기본 tick **1분**.
- 재매수 제어: `prefer_non_holding_buy=false`, `rebuy_cooldown_minutes=15`(단타 재진입 허용), 일일 종목 cap 기본 해제(0).

## 완료 ([코인] 추천·사이징 단타화)

- `crypto_recommendation.py` BUY rank에 단기 모멘텀 보너스 반영(`signed_change_rate` 가중).
- `crypto_position_sizing.py` TP/SL 기본 소스를 펀드 기준에서 코인 단타 기본값으로 전환.
- outcome 튜닝 밴드: TP **1~4%**, SL **−3~-0.8%**.

## 완료 ([코인] P0~P4 제어 로직)

- BUY 품질 게이트에 `concentration`(warn/block) 추가: `(현재평가+주문)/총자산` 기준으로 과집중 종목 매수 차단.
- `build_crypto_recommendation`에 `exclude_markets`·`prefer_non_holding_buy` 추가: 보유 외 종목 우선, 쿨다운 종목 제외.
- `crypto_auto_runner` 상태 확장: `last_buy_by_market`, `buy_markets_today`, `buy_krw_today`, 일자 롤오버.
- 러너 실행 제어: 동일 종목 재매수 쿨다운(기본 180분), 일일 신규 종목 수 cap, 일일 BUY 금액 cap, 동일 종목 당일 중복 BUY 차단.
- 과집중 보유 시 SELL 후보 보강: TP/SL 미도달이어도 초과 비중 종목을 `near_take_profit` 리뷰 후보로 반환.
- CLI(`crypto-auto-runner`) 옵션 추가: `--rebuy-cooldown-minutes`, `--max-distinct-buy-markets-per-day`, `--max-buy-krw-per-day`, `--prefer-non-holding-buy/--no-prefer-non-holding-buy`.

## 완료 ([코인] crypto_position_sizing)

- 스냅샷: `outputs/CRYPTO_ACTIVE_SIZING.json`. CLI `--max-order-value 0` / `--max-orders-per-day 0` = 자동(기본).

## 완료 ([코인] Telegram UX — 21:00 일일요약·메뉴 승인·무추천 무알림)

## 완료 ([코인] Telegram UX)

- **일일 요약**: `maybe_send_crypto_daily_summary` — **21:00 KST** 시간대에 하루 1회만 전송.
- **메뉴 추천**: 「국내(KIS)」「코인」버튼 — 추천/주문안 있으면 **승인·거부** 인라인 버튼 별도 메시지.
- **10분 tick**: 추천 없으면 Telegram **미전송** (artifacts만 저장). 매매·자동체결 시에만 알림.
- `CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL=true` 시 tick은 무승인 자동 실행, 메뉴는 수동 승인 버튼 유지.

## 완료 ([코인] 24h 무승인 자동매매 + 10분 분석 tick)

## 완료 ([코인] CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL)

- `.env`: `CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL=true` (또는 `DEEPSIGNAL_CRYPTO_AUTO_EXECUTE`) → Telegram 승인 버튼 없이 즉시 Upbit 실행, 결과만 보고.
- 기존 `DEEPSIGNAL_INACTIVE_AUTO_EXECUTE`는 **20:00~09:00** 구간용(주식·코인 공통). 코인 24h는 위 플래그 사용.
- 메뉴 **「현재 추천 보기 — 코인」** = 분석 텍스트만(승인 버튼 없음). 자동 주문·승인 메시지는 **auto-runner 분석 tick**에서만.
- launchd 분석 주기: **10분** (`--interval-minutes 10`).

## 완료 ([코인] Upbit KRW 전 종목 universe)

## 완료 ([코인] market universe — all_krw)

- `crypto_universe.py` — `core`(BTC/ETH/XRP) / `all_krw`(Upbit KRW 전체 → 24h 거래대금 필터 → 상위 `max_buy_scan_markets`개 RSI·ATR 스캔).
- 기본 `analysis_conditions.crypto.market_universe=all_krw`, `max_buy_scan_markets=20`.
- `crypto-daily-plan` / `crypto-auto-runner` / Telegram 추천 / launchd argv: `--crypto-universe`, `--max-scan-markets`, `--crypto-markets`(고정 목록).
- `crypto-check` — universe 종목 수·스캔 샘플 출력. 스냅샷: `outputs/CRYPTO_UNIVERSE_SNAPSHOT.json`.

## 완료 ([코인] Telegram 메뉴 즉시 응답)

- `crypto-auto-runner` — 30분 분석 tick과 **분리**, 기본 **4초**마다 `poll_telegram_updates_once` (메뉴 텍스트 + 승인 callback).
- `CRYPTO_TELEGRAM_OFFSET.json` — `last_update_id` 단일 offset (callback/message 동시 처리 후 저장).
- 메뉴: **현재 내 자산 보기** = KIS(DB) + Upbit / **추천** = 국내(KIS)·코인 선택.
- 로그: `crypto_auto_runner.log`에 `[menu] update received | command matched | response sent | ignored`.
- CLI: `crypto-telegram-menu --poll-once --network`.

## 완료 ([코인] technical / macro / gates / breakdown)

- `crypto_signal_scorer.py` — technical·macro·final (`SignalScorer`), `build_crypto_score_breakdown`, `load_crypto_macro_context`(deepsignal DB / fallback).
- `crypto_recommendation_quality.py` — `validation` / `liquidity` gate, `apply_crypto_buy_quality_gates`, 매도 breakdown.
- `crypto_recommendation.py` — 후보 스코어링 후 gate 통과분만 선정; `CryptoRecommendation`·plan·outcomes DB에 score/gate JSON.
- `crypto_recommendation_diagnostics.py` — 동일 scorer/gate로 BUY 후보별 `final_score`, `validation_gate`, `liquidity_gate` 출력.
- Telegram 매수 승인 문구에 final/tech/macro/regime/gates 표시.
- `analysis_conditions.crypto`: `min_final_score`, `min_acc_trade_price_24h`, `block_buy_on_risk_off` 등.

## 완료 ([코인] outcome 튜닝 + Telegram 메뉴)

- `crypto_outcome_threshold_tuning.py` — `crypto_recommendation_outcomes.db` 실현 수익률 기반 `take_profit_pct` / `stop_loss_pct` / `min_volume_ratio` 자동 튜닝 → `CRYPTO_ACTIVE_THRESHOLDS.json`.
- `crypto-auto-runner` — 하루 1회 튜닝 후 활성 임계값 적용.
- `crypto_telegram_menu.py` — 임의 메시지 시 메뉴 키보드, **현재 내 자산 보기** / **현재 추천 보기**(즉시 분석).
- CLI: `crypto-tune-thresholds`, `crypto-telegram-menu` (`--send-menu`).

## 완료 ([코인] 익절 근접·429 재시도)

- `take_profit_buffer_pct` / `stop_loss_buffer_pct` (기본 0.05%p) → `near_take_profit` / `near_stop_loss` SELL.
- Upbit `get_tickers` 배치 조회 + HTTP 429 재시도(0.5s/1s/2s, 최대 3회).
- CLI `--min-volume-ratio`, `--take-profit-buffer-pct`, `--stop-loss-buffer-pct` (`crypto-daily-plan`, `crypto-auto-runner`).
- Telegram 익절 근접 매도 승인 문구.

## 완료 ([코인] no-recommendation diagnostics)

- `crypto_recommendation_diagnostics.py` — BUY/SELL 후보별 RSI·volume·ATR·익절/손절 진단.
- 추천 없어도 `CRYPTO_ORDER_PLAN.json` (`CRYPTO_PLAN_NO_RECOMMENDATION`) + `CRYPTO_DAILY_TRADE_PLAN.md` 생성.
- CLI: `--debug-quality` JSON 전체 출력; `crypto-auto-runner` 추천 없음 Telegram 사유 보고.

## 완료 ([코인] recommendation outcome tracking)

- `outputs/crypto_recommendation_outcomes.db` — 추천·체결·실현 손익 (KIS `recommendation_outcomes.db`와 분리).
- `crypto-daily-plan` / `crypto-auto-runner`(30분 tick) / `crypto-telegram-approval`·`inactive_auto_execute` 체결 시 DB 갱신.
- `weekly-maintenance` → `CRYPTO_RECOMMENDATION_PERFORMANCE.md`.
- Telegram 일일 요약: `maybe_send_crypto_daily_summary` (runner state `last_daily_summary_date`).

## 완료 ([자동분석] peak·집중도·밸류·뉴스궤적 run-daily 통합)

## 완료 ([자동분석] full analysis automation)

- `position_peak_tracker`: `live-sync-account` 시 `position_price_peaks` DB에 고점가 자동 갱신 → `risk-check`/`sell-plan` 고점 DD -20% 적용.
- `portfolio_concentration`: 실계좌 IPS **5%** 단일 종목 비중 자동 검사 → `risk-check`·`run-daily` full-analysis.
- `ValuationAnalyzer`(yfinance): PER/PBR/성장 근사 내재가치·mispricing → `score-symbol`/`run-daily`에 **10%** 가중 반영.
- `SentimentAnalyzer`: 한국어 키워드 + 뉴스 **궤적**(악화/개선) 반영.
- `symbol_signal_builder`: score-symbol·paper-step 동일 신호(뉴스+거시+밸류).
- `run-daily` 기본 **full_analysis=True** → `AUTO_ANALYSIS_SUMMARY.md`; `--sync-live`로 KIS 동기화+peak 포함.

## 완료 ([분석조건] institutional numeric thresholds)

- `deepsignal/scoring/analysis_conditions.py`에 점수·기술·거시·리스크·포트·비용·코인 숫자 조건을 통합했다.
- `SignalScorer` / `MacroScorer` / `RiskGuardPolicy` / `sell-plan` / `PortfolioEngine` / `PaperRebalanceConfig` / AI 추천·검증·코인 품질이 동일 출처를 참조한다.
- `risk-check`·`sell-plan`에 **고점 대비 -20%** 리뷰(`position.raw.peak_price` 또는 `high_price` 있을 때)와 **진입 -10%** 리뷰(손절 -7%보다 완만한 구간)를 추가했다.
- CLI `show-analysis-conditions` → `outputs/ANALYSIS_CONDITIONS.md`, `analysis_conditions_*.json` (주문·네트워크 없음).

최종 갱신(이전): **[비활동 자동매매] 20:00~09:00 무승인 실행**

## 완료 ([실전-1]~[실전-13])

- 승인형 KIS LIMIT BUY·가드·audit·runbook·`--require-pre-trade-runbook`
- **risk-check** (standalone CLI) + **post-trade-runbook 5단계 risk_check** — 손절/익절 경고만, SELL 없음
- `POST_TRADE_OK` / `WARNING` / **`RISK_ALERT`** / `BLOCKED`

## 완료 ([macOS 포팅])

- Windows용 백업 완료 상태를 전제로, macOS 전용 실행 환경을 추가했다.
- `requirements-macos.txt` 및 `scripts/setup_macos.sh`, `scripts/test_macos.sh`, `scripts/run_live_precheck_macos.sh` 추가.
- 프로젝트 루트 기준 `.env` 로딩을 고정해 macOS shell 실행 위치 차이에 덜 민감하게 했다.
- 실주문 실행 스크립트는 만들지 않음. 실전 주문은 기존 `live-approve` guard 기반 CLI에서만 수행.

## 완료 ([macOS-2])

- README macOS 운영 섹션에 `.venv` 필수 사용, `KIS_ENV=live` 주의, dashboard Tk 제한, precheck 스크립트 사용 원칙을 보강했다.
- `docs/MACOS_OPERATION_GUIDE.md`, `docs/MACOS_TROUBLESHOOTING.md`, `docs/REAL_TRADING_CHECKLIST.md` 추가.
- 실주문 자동화 금지와 `live-approve --execute` guard 기반 수동 절차를 문서화했다.

## 완료 ([KIS 토큰 안정화])

- KIS OAuth access token 파일 캐시를 추가해 `kis-check --network` 이후 조회 CLI의 `tokenP` 반복 호출을 줄였다.
- 기본 캐시 경로는 `outputs/.kis_token_cache.json`이며, access token·만료시각·env·app key hash만 저장한다.
- app secret, 계좌번호, app key 원문은 저장하지 않는다. 실주문 guard와 `order-cash` POST 경로는 변경하지 않았다.

## 완료 ([KIS 계좌 동기화 디버깅])

- `live-sync-account` / `reconcile-live-account`의 KIS 잔고조회 position 파싱을 `pdno` 6자리 + `hldg_qty > 0` 기준으로 명확히 했다.
- `hldg_qty`가 없을 때만 `ord_psbl_qty`를 fallback으로 쓰며, `quantity <= 0`은 실보유 포지션에서 제외한다.
- 최신 DB 포지션 조회는 최신 `real_account_snapshots.snapshot_time` 기준으로 수행한다. 최신 스냅샷에 포지션 0개가 저장되면 과거 `real_positions`를 최신처럼 읽지 않는다.
- `live-sync-account --debug-raw`, `reconcile-live-account --debug-raw`를 추가해 KIS `output1`/`output2` 행 수와 키 목록만 안전하게 확인할 수 있다.

## 완료 ([실전-14])

- `ops-dashboard` CLI를 추가해 최신 실계좌 운영 상태를 단일 JSON/Markdown으로 요약한다.
- 입력은 로컬 DB 최신 `real_account_snapshots`·`real_positions`·최근 `real_order_history`, 그리고 `outputs/` 최신 reconcile/risk/fill 리포트다.
- 상태는 `OK` / `WARNING` / `RISK_ALERT` / `RECONCILE_MISMATCH` / `NO_DATA`로 판정한다.
- 산출물은 `outputs/ops_dashboard_YYYYMMDD_HHMMSS.json`, `outputs/OPS_DASHBOARD.md`이며, 조회/요약 전용이다. SELL·시장가·자동 반복·자동 취소·KIS 주문 POST는 없다.

## 완료 ([실전-15])

- `sell-plan` CLI를 추가해 최신 `real_positions`와 리스크 기준으로 운영자 검토용 SELL 계획서를 생성한다.
- 산출물은 `outputs/sell_plan_YYYYMMDD_HHMMSS.json`, `outputs/SELL_PLAN.md`이다.
- 상태는 `HOLD` / `REVIEW` / `REDUCE` / `EXIT` / `NO_DATA`로 판정한다.
- `stop_loss_pct` 이하면 `EXIT`, `warn_loss_pct` 이하면 `REVIEW`, `take_profit_pct` 이상이면 `REDUCE`, 그 외는 `HOLD` 제안이다.
- 계획서 생성만 수행한다. SELL API, `order-cash` SELL, 시장가, 자동매도, 자동 반복, 자동 취소, KIS 주문 POST는 없다.

## 완료 ([실전-16])

- `notify-alerts` CLI를 추가해 최신 risk/ops/sell/reconcile 리포트 기반 위험 메시지를 Telegram 또는 Discord로 보낼 수 있다.
- 기본은 dry-run이며, `--send` 없이는 네트워크 호출을 하지 않는다.
- 알림 source는 `risk_alert_*.json`, `ops_dashboard_*.json`, `sell_plan_*.json`, `reconcile_live_account_*.json` 최신 파일이다.
- 산출물은 `outputs/notification_audit_YYYYMMDD_HHMMSS.json`이며, dry_run/channel/messages/results와 `실제_주문_없음=true`를 기록한다.
- alert-only 기능이다. 주문 실행, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS 주문 POST는 없다.

## 완료 ([실전-17])

- `daily-ops-summary` CLI를 추가해 당일 운영 산출물을 단일 JSON/Markdown으로 통합한다.
- 입력은 최신/오늘자 `live_account_snapshot`, `reconcile`, `risk`, `ops-dashboard`, `sell-plan`, `notification_audit` 파일이다.
- 상태는 `OK` / `WARNING` / `RISK_ALERT` / `RECONCILE_MISMATCH` / `NO_DATA`로 판정하고 next actions를 생성한다.
- 오늘 파일이 없으면 기본적으로 latest fallback을 사용하고 warnings에 기록한다.
- `--notify-dry-run`으로 네트워크 없이 notification audit을 먼저 생성해 포함할 수 있다.
- 조회/요약 전용이다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST는 없다.

## 완료 ([실전-18])

- `html-dashboard` CLI를 추가해 `outputs/` 최신 운영 JSON을 단일 정적 HTML로 시각화한다.
- 입력은 `daily_ops_summary`, `ops_dashboard`, `risk_alert`, `sell_plan`, `reconcile_live_account`, `live_account_snapshot`, `live_fill_summary`, `notification_audit` 파일이다.
- 산출물은 `outputs/OPS_DASHBOARD.html`이며, Overall/Risk/Reconcile/Sell Plan 카드와 Account, Positions, Reconcile, Risk Alerts, Sell Plan, Recent Orders/Fills, Notifications, Next Actions 섹션을 포함한다.
- HTML은 inline CSS만 사용한다. 웹서버, 네트워크 호출, 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST는 없다.

## 완료 ([실전-19])

- `post-trade-runbook --with-summary` / `--full-report` 옵션을 추가해 사후 리포트 체인을 선택 실행할 수 있다.
- 기본 `post-trade-runbook` 동작은 기존처럼 order status → fill summary → account sync → reconcile → risk-check → summary까지만 수행한다.
- `--with-summary`일 때는 risk-check 이후 `ops-dashboard`, `sell-plan`, `daily-ops-summary`, `html-dashboard`를 순서대로 생성한다.
- post-trade runbook JSON/Markdown의 `summary.generated_reports`에 risk, ops dashboard, sell plan, daily summary, HTML dashboard 경로를 기록한다.
- 추가 리포트 생성 실패는 critical failure가 아니라 warning으로 기록한다. 기존 reconcile mismatch·risk alert 정책은 유지한다.
- 조회/요약/파일 생성 전용이다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, 웹서버, KIS POST는 없다.

## 완료 ([실전-20])

- `cleanup-reports` CLI를 추가해 `outputs/` 리포트 보존/정리를 dry-run 기본으로 수행한다.
- 보존 정책은 `--keep-days`와 카테고리별 `--keep-latest`를 함께 적용한다.
- `--archive` 사용 시 삭제 대신 `outputs/archive` 등 `output_dir` 내부 archive 디렉터리로 이동한다.
- `OPS_DASHBOARD.html`, 최신 Markdown 요약 파일, `.gitkeep`, `.kis_token_cache.json` 등 중요 파일은 보호한다.
- macOS AppleDouble `._*` 파일은 후보로 표시하며, 실제 삭제는 `--apply --remove-appledouble`일 때만 수행한다.
- `report_cleanup_audit_YYYYMMDD_HHMMSS.json`에 candidates/deleted/archived/kept/warnings와 `network_called=false`, `실제_주문_없음=true`를 기록한다.
- 파일 정리 전용이다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, 네트워크 호출, KIS POST는 없다.

## 완료 ([실전-21])

- `report-index` CLI를 추가해 `outputs/`와 선택한 archive 디렉터리의 운영 리포트를 정적 HTML/Markdown/JSON 인덱스로 생성한다.
- 입력은 daily summary, ops dashboard, risk, sell plan, reconcile, account snapshot, notification audit, pre/post runbook, fill summary, cleanup audit, 주요 HTML/Markdown 리포트다.
- 날짜별 grouping, 카테고리별 grouping, recent reports 목록과 상대 링크를 생성한다.
- JSON 리포트는 원문 전체를 복사하지 않고 status/count 중심 summary만 포함한다.
- 산출물은 `outputs/REPORT_INDEX.html`, `outputs/REPORT_INDEX.md`, `outputs/report_index_YYYYMMDD_HHMMSS.json`이다.
- 정적 인덱스 생성 전용이다. 웹서버, 네트워크 호출, 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST는 없다.

## 완료 ([실전-22])

- `ops-dry-run` CLI를 추가해 세션 확인, KIS offline 설정 검증, risk-check, ops-dashboard, sell-plan, daily-ops-summary, html-dashboard, report-index를 한 번에 실행한다.
- 기본 실행은 네트워크 없이 로컬 DB/outputs 기반 조회·리포트 생성만 수행한다.
- `--network` 사용 시에만 KIS OAuth 확인, live account sync, reconcile 조회 단계를 추가한다.
- 산출물은 `outputs/ops_dry_run_YYYYMMDD_HHMMSS.json`, `outputs/OPS_DRY_RUN.md`이다.
- 장외/주말 session closed는 warning으로 기록하고 리포트 생성을 계속한다.
- reconcile mismatch·risk alert·단계 실패를 최종 상태(`OPS_DRY_RUN_WARNING` / `OPS_DRY_RUN_BLOCKED`)에 반영한다.
- 조회/점검/파일 생성 전용이다. `live-approve` 호출, 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS 주문 POST는 없다.

## 완료 ([실전-23])

- `open-dashboard` CLI를 추가해 `outputs/`의 주요 운영 리포트 경로를 한 번에 안내한다.
- 기본 대상은 `OPS_DASHBOARD.html`, `REPORT_INDEX.html`, `DAILY_OPS_SUMMARY.md`, `OPS_DRY_RUN.md`, `RISK_ALERT.md`, `SELL_PLAN.md`이다.
- 기본 실행은 파일 목록 출력만 수행하고 브라우저를 열지 않는다.
- `--open`은 `OPS_DASHBOARD.html`, `--open-index`는 `REPORT_INDEX.html`, `--open-all`은 존재하는 HTML 리포트만 연다.
- `output_dir` 내부 로컬 파일만 `file://`로 열며, 웹서버·외부 URL·네트워크 호출은 없다.
- 보기 편의 전용이다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST는 없다.

## 완료 ([실전-24])

- `post-trade-runbook` CLI에 `risk-check`와 동일한 risk threshold 옵션을 추가했다.
- 추가 옵션은 `--stop-loss-pct`, `--take-profit-pct`, `--warn-loss-pct`, `--warn-profit-pct`이며 기본값은 기존 `RiskGuardPolicy`와 동일하다.
- `run_post_trade_runbook(..., risk_policy=...)`로 policy를 전달할 수 있고, None이면 기존 기본 정책을 사용한다.
- post-trade risk_check 단계는 지정 policy를 `run_portfolio_risk_check`에 전달한다.
- post-trade JSON summary와 Markdown에 `risk_policy` / `Risk Policy`를 기록한다.
- `risk-check`와 `post-trade-runbook`이 공통 `risk_policy_from_namespace()` helper를 사용한다.
- threshold 전달만 수행한다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST 변경은 없다.

## 완료 ([실전-25])

- `report-health-check` CLI를 추가해 `outputs/` 운영 리포트, 최신 JSON 나이, DB 최신 account snapshot/positions, AppleDouble, KIS token cache, stale dashboard, output 파일 수를 한 번에 진단한다.
- 상태는 `HEALTH_OK` / `HEALTH_WARNING` / `HEALTH_CRITICAL` / `HEALTH_NO_DATA`로 판정한다.
- 산출물은 `outputs/report_health_YYYYMMDD_HHMMSS.json`, `outputs/REPORT_HEALTH.md`이며, issues와 next actions를 기록한다.
- 진단 전용이다. 파일 삭제, cleanup 실행, 네트워크 호출, 알림 전송, 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST는 없다.

## 완료 ([실전-26])

- `weekly-maintenance` CLI를 추가해 주간 운영 점검을 dry-run으로 묶었다.
- 실행 단계는 `report-health-check` → `cleanup-reports` dry-run → `daily-ops-summary` → `html-dashboard` → `report-index` → weekly summary 생성이다.
- 산출물은 `outputs/weekly_maintenance_YYYYMMDD_HHMMSS.json`, `outputs/WEEKLY_MAINTENANCE.md`이다.
- 상태는 `WEEKLY_MAINTENANCE_OK` / `WEEKLY_MAINTENANCE_WARNING` / `WEEKLY_MAINTENANCE_CRITICAL`로 판정한다.
- `--apply`, `--archive`, `--network`, `--send` 옵션은 만들지 않았다. 삭제, archive 이동, 네트워크 호출, 알림 전송, 실주문, SELL 자동화, KIS POST는 없다.

## 완료 ([실전-27])

- `notify-alerts --include-maintenance` 옵션을 추가해 `weekly_maintenance_*.json`과 `report_health_*.json`을 opt-in 알림 source로 포함할 수 있게 했다.
- `WEEKLY_MAINTENANCE_WARNING`은 `WARNING`, `WEEKLY_MAINTENANCE_CRITICAL`은 `CRITICAL` 메시지로 변환한다.
- `HEALTH_WARNING` / `HEALTH_NO_DATA`는 `WARNING`, `HEALTH_CRITICAL`은 `CRITICAL` 메시지로 변환한다.
- OK 상태는 기본 제외하며, `--include-ok`일 때만 `INFO`로 포함한다.
- 기본은 dry-run이라 `--send` 없이는 네트워크 호출이 없고, notification audit에 `source_file`, `maintenance_status` / `health_status` metadata를 남긴다.
- 알림 전용이다. 실주문, SELL 자동화, KIS POST, cleanup apply, archive 이동은 없다.

## 완료 ([실전-28])

- `weekly-report-bundle` CLI를 추가해 주간 운영 결과를 `outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/` 폴더로 복사해 묶는다.
- 기본 실행은 `weekly-maintenance`, `notify-alerts --dry-run --include-maintenance`, `report-index`, `html-dashboard`를 네트워크 없이 실행한 뒤 핵심 Markdown/HTML/JSON만 번들링한다.
- 번들 내부에 `BUNDLE_INDEX.md`, `BUNDLE_INDEX.html`을 생성한다.
- `--zip` 옵션이 있을 때만 `weekly_bundle_YYYYMMDD_HHMMSS.zip`을 생성하고, 기본은 ZIP 없음이다.
- `.env`, `.kis_token_cache.json`, DB 파일, 소스 코드, API key/app secret/token cache는 번들 대상에서 제외한다.
- 리포트 생성/복사/번들링 전용이다. 삭제, archive 이동, 네트워크 호출, 알림 실제 전송, 실주문, SELL 자동화, KIS POST는 없다.

## 완료 ([실전-29])

- `generate-checklists` CLI를 추가해 daily/pre-market/post-trade/weekly maintenance/safety Markdown 체크리스트를 생성한다.
- 산출물은 `outputs/checklists/DAILY_CHECKLIST.md`, `PRE_MARKET_CHECKLIST.md`, `POST_TRADE_CHECKLIST.md`, `WEEKLY_MAINTENANCE_CHECKLIST.md`, `SAFETY_RULES.md`이다.
- 체크리스트는 운영자 참고용 템플릿이며 실제 스케줄러가 아니다.
- cron, launchd, plist 생성, 자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST는 없다.
- safety rules에 `live-approve --execute` 자동화 금지, `--final-confirm` 자동 주입 금지, `.env` 커밋 금지, `KIS_ENV=live` 확인, 시장가 금지, KIS POST 직접 호출 금지를 명시했다.

## 완료 ([실전-30])

- `safety-audit` CLI를 추가해 체크리스트, `SAFETY_RULES.md` 필수 문구, 주요 운영 리포트, 최신 reconcile/account/risk/fill/audit 파일을 로컬에서 점검한다.
- 산출물은 `outputs/safety_audit_YYYYMMDD_HHMMSS.json`, `outputs/SAFETY_AUDIT.md`이다.
- 상태는 `SAFETY_AUDIT_OK` / `SAFETY_AUDIT_WARNING` / `SAFETY_AUDIT_BLOCKED`이며, OK/WARNING은 종료 코드 0, BLOCKED는 1이다.
- `--strict` 옵션은 WARNING을 BLOCKED로 승격한다.
- 점검은 읽기 전용이며 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, 시장가 주문 기능, cron/launchd/plist/alias/shell script 생성, cleanup apply, archive 이동, 파일 삭제는 없다.
- `.env` 값, 토큰, app secret, 계좌번호 원문은 출력하지 않는다.

## 완료 (AI_CONTEXT Bootstrap Manager)

- `project_context` 패키지를 추가해 프로젝트 metadata 스캔, Markdown 템플릿 생성, overwrite 없는 `AI_CONTEXT` 파일 생성을 분리했다.
- `init-context` CLI를 추가했다.
- 지원 명령은 `python main.py init-context`, `python main.py init-context --project PATH`, `python main.py init-context --project PATH --all-projects`이다.
- 생성 대상은 `PROJECT_CONTEXT.md`, `CURRENT_STATUS.md`, `TODO.md`, `KNOWN_ISSUES.md`, `RULES.md`, `PROMPT_HISTORY.md`, `RESULT_HISTORY.md`이다.
- 기존 파일은 overwrite하지 않고 없는 Markdown만 생성한다.
- metadata 추론은 로컬 README, dependency 파일, source/test/docs 디렉토리, 파일 확장자 기반 언어 감지만 사용한다. 네트워크/LLM/git/shell/destructive operation은 없다.

## 완료 ([실전-31])

- `report-index`에 Safety Audit 섹션을 추가해 `SAFETY_AUDIT.md`, 최신 `safety_audit_*.json`, status, updated time, warning/blocked count를 표시한다.
- `html-dashboard`에 Safety Audit 카드/섹션을 추가해 status, audit time, Markdown/JSON 링크, warning/blocked count를 표시한다.
- `open-dashboard` 목록에 `SAFETY_AUDIT.md`와 최신 `safety_audit_*.json` 경로를 추가했다.
- Safety Audit 파일이 없어도 dashboard/index 생성은 실패하지 않고 `NOT_AVAILABLE` 또는 “Safety audit has not been generated yet”로 표시한다.
- 모든 연동은 로컬 파일 링크 전용이며 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, cleanup apply, archive 이동, 파일 삭제는 없다.

## 완료 ([실전-32])

- `archive-viewer` CLI를 추가해 `outputs/`와 `outputs/archive/`의 과거 운영 리포트를 read-only 로컬 HTML/JSON으로 인덱싱한다.
- 산출물은 `outputs/ARCHIVE_VIEWER.html`, `outputs/archive_viewer_YYYYMMDD_HHMMSS.json`이다.
- 대상은 safety audit, weekly maintenance, report health, risk, reconcile, live account snapshot, live approval audit, fill summary, cleanup audit, weekly bundle index, 주요 Markdown/HTML 리포트다.
- `.env`, token cache, DB 파일, source code는 제외한다.
- `report-index`에 Archive Viewer 섹션을 추가했고, `open-dashboard` 목록과 `--open-archive` 옵션을 추가했다.
- 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, 시장가 주문 기능, cleanup apply, archive 이동, 파일 삭제, DB 내용 읽기, source code 분석은 없다.

## 완료 ([실전-33])

- `ARCHIVE_VIEWER.html`에 report type/status/severity/text/date range 필터와 Only warnings/errors, Latest only 토글을 추가했다.
- Modified Time/Type/Status/Severity/Size 컬럼 정렬을 지원하며 기본 정렬은 최신 Modified Time DESC다.
- Summary에 latest risk alert, reconcile, live approval status를 추가했다.
- `Needs Attention` 섹션을 추가해 warning 이상, blocked/error/failed 계열, reconcile mismatch, partial fill open, stale snapshot, safety audit blocked 리포트를 빠르게 링크한다.
- `archive_viewer_*.json`에 `summary`, `filters_available`, `entries`, `needs_attention`, `latest_by_type` 구조를 명시적으로 포함한다.
- 기능은 read-only 로컬 viewer이며 네트워크 호출, 실주문, 자동 복구, cleanup apply, archive 이동, 파일 삭제, DB 내용 읽기, source code 분석은 없다.

## 완료 ([실전-34])

- `operator_labels.py`를 추가해 report type/status/severity/summary key의 한국어 운영자 표시 label을 분리했다.
- `ARCHIVE_VIEWER.html`의 제목, summary, 필터, table header, Needs Attention, badge 표시를 한국어 중심으로 바꿨다.
- JSON export와 `data-type`/`data-status`/`data-severity`는 기존 raw machine-readable 값을 유지한다.
- `report-index`와 `OPS_DASHBOARD.html`의 Safety Audit/Archive Viewer 관련 표시 일부도 한국어 label을 병기한다.
- 기능은 표시 문구 변환 전용이며 네트워크 호출, 실주문, 자동 복구, cleanup apply, archive 이동, 파일 삭제, JSON raw status 변경은 없다.

## 완료 ([실전-35])

- `ARCHIVE_VIEWER.html`에 print-friendly `@media print` CSS를 추가해 필터 UI를 숨기고 summary, needs attention, table 중심으로 인쇄되도록 했다.
- `archive-viewer` 기본 실행 시 `outputs/ARCHIVE_VIEWER.csv`와 `outputs/ARCHIVE_VIEWER_SUMMARY.md`를 함께 생성한다.
- CSV는 report type/status/severity raw 값과 한국어 label, modified time, size, relative path, title 등 metadata만 포함한다.
- Markdown summary는 생성 시각, 운영 요약, 최근 상태, 유형별 최신 리포트, 주의 필요 항목, 주요 리포트 링크를 포함한다.
- `--no-csv`, `--no-summary-md` 옵션을 추가했고, `archive_viewer_*.json`에 `export_files`를 기록한다.
- `report-index`와 `open-dashboard`에 CSV/Markdown summary 링크/경로를 추가했다.
- 리포트 원문 전체 export, 네트워크 호출, 실주문, 자동 복구, cleanup apply, archive 이동, 파일 삭제는 없다.

## 완료 ([실전-36])

- `archive-viewer` 실행 시 `outputs/ARCHIVE_VIEWER_PRESETS.json` 정적 preset 파일을 생성한다.
- 기본 preset은 `needs_attention`, `latest_only`, `safety_audit`, `risk_and_reconcile`, `live_order_audit`이다.
- `ARCHIVE_VIEWER.html` 필터 영역에 preset select, 프리셋 적용, 필터 초기화 UI를 추가했다.
- HTML은 preset JSON을 inline으로 embed하고, 기존 filter input/select/toggle을 조정하는 방식으로 적용한다. 외부 fetch/localStorage/server는 사용하지 않는다.
- `archive_viewer_*.json`에 `presets`, `preset_file`, `export_files.presets`를 추가했다.
- Markdown summary, report-index, open-dashboard에 preset 파일/목록을 연결했다.
- 네트워크 호출, DB 조회, 리포트 원문 전체 export, 실주문, 자동 복구, cleanup apply, archive 이동, 파일 삭제는 없다.

## 완료 ([실전-37])

- Archive Viewer entries metadata를 기반으로 `trend_analytics`를 계산한다.
- JSON export에 `by_day`, `by_report_type`, `by_severity`, `by_status`, 최근 경고/차단 trend, 유형별 주의 항목, 반복 문제 유형을 추가했다.
- `ARCHIVE_VIEWER.html`과 `ARCHIVE_VIEWER_SUMMARY.md`에 `운영 추세` 섹션을 추가했다.
- `archive-viewer --trend-days N` 옵션을 추가했으며 기본값은 7이다.
- Trend Analytics는 과거 로컬 리포트 metadata 통계이며 실계좌 상태 재조회, 실주문, 자동 복구 기능이 아니다.

## 완료 ([실전-37] AI Live Trade Recommendation Engine)

- `deepsignal/live_trading/ai_recommendation/` 패키지와 `ai-live-recommend` CLI를 추가했다.
- 최신 signals, market price, macro score, 실계좌 snapshot, reconcile/risk/fill/safety/archive trend metadata를 기반으로 BUY/SELL/HOLD/REDUCE/INCREASE/SKIP 추천을 생성한다.
- 산출물은 `AI_LIVE_TRADE_RECOMMENDATION.md`, `ai_live_trade_recommendation_YYYYMMDD_HHMMSS.json`, `live_order_plan_ai_YYYYMMDD_HHMMSS.json`이다.
- `live_order_plan_ai`는 `PENDING_APPROVAL`, `approval_required=true`, `dry_run=true`이며 기존 `live-approve` 수동 승인 경로와 호환되는 BUY/LIMIT 주문안만 포함한다.
- SELL/REDUCE 후보는 리포트에 표시하지만 기본 주문안에서는 제외한다. `live-approve` 자동 호출, `--execute` 자동 호출, KIS `order-cash` POST, 시장가 주문, final-confirm 자동 주입은 없다.

## 완료 ([실전-38] AI Recommendation Backtest / Paper Validation)

- `validate-ai-recommendation` CLI를 추가해 `ai-live-recommend` v1 정책을 로컬 DB 기준으로 검증한다.
- 입력은 `market_prices`, `signals`, macro metadata이며 날짜별로 in-memory portfolio를 재생한다.
- 기본은 BUY/INCREASE만 가상 반영하고, `--include-sell-reduce`일 때만 SELL/REDUCE를 반영한다.
- 산출물은 `AI_RECOMMENDATION_VALIDATION.md`, `ai_recommendation_validation_YYYYMMDD_HHMMSS.json`, `AI_RECOMMENDATION_VALIDATION_TRADES.csv`이다.
- KIS 호출, live-approve 호출, `--execute`, 실계좌 주문, KIS POST, 실계좌 테이블 수정, `paper_*` 운영 테이블 수정은 없다.

## 완료 ([실전-39] AI Recommendation Advanced Validation Metrics)

- `validate-ai-recommendation`에 advanced metrics와 benchmark 비교를 추가했다.
- 지표는 annualized return, volatility, Sharpe, max drawdown 구간, profit factor, expectancy, win/loss rate, 연속 손익, exposure, turnover, average holding days, best/worst trade, action/symbol별 PnL을 포함한다.
- benchmark는 동일 기간 대상 symbols 동일비중 buy-and-hold이며 `--benchmark` 기본 ON, `--risk-free-rate` 기본 0.0이다.
- equity curve는 daily return, drawdown, exposure를 포함하고 trades CSV에는 realized PnL, holding days, action group을 추가했다.
- 검증은 여전히 로컬 DB read-only + in-memory portfolio이며 KIS/live-approve/실계좌/paper_* 수정은 없다.

## 완료 ([실전-40] AI Recommendation Cost / Slippage Validation)

- `validate-ai-recommendation`에 비용 모델을 추가해 수수료, 세금, 슬리피지, 최소/최대 주문금액을 검증에 반영한다.
- 기본 비용 가정은 commission 0.1%, tax 0%, slippage 5bps, min order 10,000 KRW이며 `--no-costs`로 비용 모델을 비활성화할 수 있다.
- validation JSON/Markdown/CSV에는 비용 차감 전/후 수익률, 총 수수료/세금/슬리피지, 비용 drag, 스킵된 주문 metadata, 거래별 raw/adjusted price와 비용 컬럼이 포함된다.
- benchmark에도 비용 적용 여부와 benchmark total cost/net return metadata를 기록한다.
- 검증은 계속 로컬 DB read-only + in-memory portfolio이며 KIS/live-approve/`--execute`/실계좌 주문/paper_* 운영 테이블 수정은 없다.

## 완료 ([실전-41] AI Recommendation Portfolio Risk Validation)

- `validate-ai-recommendation`에 portfolio risk validation을 추가해 최종 in-memory 포지션 기준 단일 종목 비중, 섹터 비중, 초과 비중 종목/섹터, 고상관 종목쌍을 계산한다.
- `--sector-map` 로컬 JSON을 지원하며 파일이 없거나 심볼이 없으면 sector는 `UNKNOWN`으로 처리한다. 네트워크/yfinance info 조회는 없다.
- `--max-symbol-weight`, `--max-sector-weight`, `--correlation-threshold`, `--correlation-lookback-days` 옵션을 추가했다.
- validation JSON에는 `portfolio_risk`, Markdown에는 `포트폴리오 리스크 검증`, CSV에는 `AI_RECOMMENDATION_PORTFOLIO_RISK.csv`가 추가된다.
- `concentration_score`와 `diversification_score`는 검증 경고용 deterministic 점수이며 `blocked`도 주문 차단이 아니라 리포트상 강한 경고다.

## 완료 ([실전-42] AI Recommendation Liquidity Constraint Validation)

- `validate-ai-recommendation`에 거래량 기반 liquidity validation을 추가했다.
- `market_prices.volume`을 읽어 최근 평균 거래량/거래대금 기준으로 주문 수량 축소 또는 스킵을 적용한다.
- `--liquidity-limit-pct`, `--min-daily-volume`, `--min-daily-value`, `--volume-lookback-days` 옵션을 추가했다.
- 기본은 liquidity 제한 없음이므로 기존 동작을 유지한다.
- validation JSON에는 `liquidity_model`, Markdown에는 `유동성 제한 검증`, trades CSV에는 liquidity requested/allowed/adjusted/skip/warning 컬럼이 추가된다.
- 외부 유동성 API, yfinance info, KIS/live-approve/`--execute`, 실계좌/paper_* 운영 테이블 수정은 없다.

## 완료 ([실전-43] AI Recommendation FX / Currency-Aware Validation)

- `validate-ai-recommendation`에 로컬 FX/currency validation을 추가했다.
- `--base-currency`, `--default-symbol-currency`, `--fx-rates`, `--symbol-currency-map`, `--fallback-fx` 옵션을 추가했다.
- 종목별 currency를 결정하고 trade value/cost/equity를 기준 통화로 환산해 JSON/Markdown/CSV에 기록한다.
- equity curve에는 `equity_base_currency`, `cash_by_currency`, `position_value_by_currency`, `fx_rates_used`를 포함한다.
- 환율 파일이 없으면 기존 단일통화 방식으로 동작하며, 외부 FX API/yfinance info/네트워크 조회는 없다.

## 완료 ([실전-44] Telegram Approval Trading Workflow)

- `telegram-approval-request`, `telegram-approval-listen`, `telegram-approval-status` CLI를 추가했다.
- 요청은 plan SHA-256, one-time token, expiry, chat id, 주문 건수/금액 한도를 로컬 JSON/Markdown/state에 기록한다.
- `--send` 또는 `listen`에서만 Telegram Bot API를 호출하며 기본 request는 dry-run이다.
- listen은 chat_id/token/expiry/plan_hash/limits/today halt를 검증한 뒤 승인/중단 audit만 생성한다.
- Telegram approval은 final-confirm을 대체하지 않으며 `execute_live_order_plan()`, `live-approve`, `--execute`, `--allow-live-env`, KIS POST를 자동 호출하지 않는다.
- 승인 완료 후에는 운영자가 직접 실행할 `execute-last-approved` 단축 명령을 Markdown/콘솔/audit에 안내한다.

## 완료 ([실전-45] Simplified Approved Execution Command)

- `execute-last-approved`, `execute-approved --request-id` CLI를 추가했다.
- 최신 또는 지정 Telegram approval audit/state를 조회해 승인 상태, token consumed, expiry, today halt, plan hash, plan 파일, 주문 한도, 중복 실행 여부를 검증한다.
- 검증 통과 시 운영자가 터미널에서 직접 실행한 명령에 한해 기존 `execute_live_order_plan()` 경로를 재사용한다.
- `EXECUTE_APPROVED_AUDIT.md`, `execute_approved_audit_*.json`에 Telegram approval linkage와 live approval linkage를 기록한다.
- Telegram listener는 계속 실행하지 않으며, Telegram 버튼만으로 실주문은 발생하지 않는다.

## 완료 ([실전-46] Daily AI Trading Workflow)

- `daily-ai-trade-plan`, `daily-ai-trade-report`, `daily-ai-status` CLI를 추가했다.
- plan 명령은 AI 추천과 `live_order_plan_ai_latest.json`, `AI_DAILY_TRADE_PLAN.md`, `ai_daily_trade_plan_*.json`만 생성하며 실주문을 실행하지 않는다.
- Telegram 승인 요청은 `outputs/live_order_plan_ai_latest.json`를 바로 사용할 수 있고, 메시지에 daily plan 경로와 주문 요약을 포함한다.
- report 명령은 최신 AI 추천/승인/실행/체결/계좌/리스크/안전/아카이브 산출물을 요약해 `AI_DAILY_TRADE_REPORT.md`, `ai_daily_trade_report_*.json`를 생성한다.
- status 명령은 plan/approval/execution/fill/report 상태와 다음 명령을 `AI_DAILY_STATUS.md`, `ai_daily_status_*.json`에 기록한다.

## 완료 ([긴급-MVP] generate-test-order-plan)

- `generate-test-order-plan` CLI로 소액 BUY/LIMIT 테스트 주문안 `outputs/test_live_order_plan.json` 생성
- `telegram-approval-request` / `execute-last-approved` 검증용 (KIS·실행 호출 없음)

## 완료 ([긴급-UX] execute-last-approved Telegram listen 통합)

- `execute-last-approved`가 Telegram `getUpdates` polling으로 승인/중단 callback을 자동 확인한다.
- 승인 시 audit/state 갱신 후 기존 `execute_live_order_plan` 경로로 이어진다.
- `--wait-seconds`(기본 60), `--poll-interval`(기본 2) 옵션을 추가했다.
- `telegram-approval-listen`은 debug/optional로 유지한다.

## 완료 ([실전-코인-01] UpbitBroker MVP)

- `deepsignal/crypto_trading/`: Upbit 잔고·시세·지정가 매수(dry-run), 추천·계획·Telegram 승인·auto-runner
- CLI: `crypto-check`, `crypto-daily-plan`, `crypto-telegram-approval`, `crypto-auto-runner`
- KIS `live_trading` 경로와 분리; 실주문은 Telegram 승인 + `--execute` 시에만
- **체결 후속**: `get_order(uuid)`, `--wait-fill-seconds` / `--fill-poll-interval`, Telegram 체결·대기·부분·취소 알림, `crypto_order_status_*.json` audit
- **매도 타이밍**: 보유 평가·익절/손절 SELL 우선 → `place_limit_sell` / Telegram 매도 승인

## 완료 ([실전-코인-launchd] crypto-auto-runner launchd)

- `install-crypto-launchd` / `uninstall-crypto-launchd` / `crypto-launchd-status` CLI
- Label `com.deepsignal.crypto_auto_runner` (KIS `com.deepsignal.auto_runner`와 분리)
- 로그: `~/.deepsignal/logs/crypto_auto_runner.log` / `.error.log`
- `#` 경로 sanitize: `~/.deepsignal/project_root` symlink, `.venv/bin/python` resolve 금지
- **launchd .env**: `crypto_env.py` — 프로젝트 `.env` + optional `~/.deepsignal/.env`, plist에 secret 없음
- `[crypto runner startup]` 로그, install 시 env 경고, status 시 UpbitConfigError 힌트
- **추천 품질**: `recommendation_quality.py` — liquidity·portfolio_risk·breakdown을 `daily-ai-trade-plan`에 연결 (`enable_quality_gates` 기본 true)
- **검증→임계값**: `validate-ai-recommendation` 후 `outputs/AI_VALIDATION_THRESHOLD_SUMMARY.json` 생성 → `daily-ai-trade-plan`이 `min_final_score` 자동 적용 (`use_validation_tuned_min_score` 기본 true)
- **추천→결과→학습**: `outputs/recommendation_outcomes.db` — `daily-ai-trade-plan` 기록, `weekly-maintenance`가 refresh + `RECOMMENDATION_PERFORMANCE.md` 자동 생성
- **[학습루프-02]**: `tune-threshold-from-outcomes` — outcomes 실전 성과로 `AI_VALIDATION_THRESHOLD_SUMMARY.json` 보정(validation과 blend), `weekly-maintenance --tune-threshold-from-outcomes` 연동
- **코인 BUY 필터**: RSI 과열·거래대금 비율·ATR 변동성 (`crypto_quality.py`), Upbit 일봉 `get_daily_candles`

## 완료 ([운영-자동시작] macOS launchd)

- `install-launchd` / `uninstall-launchd` / `launchd-status` / `launchd-runner-test` CLI
- `#` 경로: `~/.deepsignal/project_root` symlink + `~/.deepsignal/logs/` (launchd EX_CONFIG 78 회피)
- plist는 `.venv/bin/python`·`main.py` 경로를 resolve() 없이 기록

## 완료 ([운영고정] daily-ai-auto-runner)

- `daily-ai-auto-runner` 상시 루프: 09:05 plan+Telegram 승인요청, 장중 callback→실주문, 15:40 report+Telegram
- `DAILY_AI_AUTO_RUNNER_STATE.json`: 일자·pending token·telegram offset 복구
- Telegram 운영 메시지 한국어 단순화 (`telegram_operator_messages.py`, `config/symbol_name_map.json`)
- orders=0 → Telegram "오늘 주문 없음"

## 완료 ([비활동 자동매매] 20:00~09:00)

- `.env` `DEEPSIGNAL_INACTIVE_AUTO_EXECUTE=true` 시 **20:00~09:00 (KST)** 운영자 비활동 구간
- **09:00~20:00**: `KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL=off` 일 때만 Telegram 승인 버튼 (`on`이면 장중 자동 실행)
- **20:00~09:00**: 승인 요청 없이 주문 실행 → Telegram에는 **결과만** 보고 (`operator_inactive_window.py`, `inactive_auto_execute.py`)
- `daily-ai-auto-runner`: 비활동 시 plan 즉시 실행·대기 중 pending도 자동 실행
- `crypto-auto-runner` / `crypto-telegram-approval`: 비활동 시 매수·매도 승인 없이 업비트 실행 후 결과만 Telegram 보고 (`execute_crypto_plan_inactive_auto`)
- pending 코인 승인 건도 비활동 구간에 자동 실행
- 주식 KIS는 **정규장(09:00~15:30)** 밖이면 세션 가드로 실주문 실패 가능 → Telegram에 실패 사유 보고

## 완료 ([긴급-MVP] daily-ai-trade-plan Plan Orders 0 진단/완화)

- `plan_order_diagnostics.py`: 추천별 주문안 제외 사유·운영 컨텍스트 진단
- `daily-ai-trade-plan --debug-plan` / `--allow-test-plan-order` / `--ignore-safety-block-for-test`(둘 다) / `--max-order-value`
- test-plan: safety BLOCKED는 주문안만 완화, 장외 Telegram 승인 가능, 실주문은 장중 execute guard
- Plan Orders 0 시 콘솔·`AI_DAILY_TRADE_PLAN.md` 진단 표, CLI exit code 1
- `telegram-approval-request`: orders=0이면 BLOCKED + 안내 메시지

## 완료 ([실전-최종UX] Telegram 승인 즉시 실주문 자동 실행)

- `telegram_auto_execute.py`: 승인 callback → audit/state → `execute_live_order_plan` → Telegram 결과 전송
- `telegram-approval-request --send` 기본: 승인 대기 폴링 후 자동 실행 (`--no-auto-execute`로 legacy)
- `telegram-approval-listen` 기본: 승인 시 즉시 실행 (`--no-auto-execute`로 audit만)
- 승인 Telegram 메시지: 종목·수량·지정가·AI 신뢰도·사유, [승인]/[거부]
- AI 주문안: `limit_price`, `ai_confidence`, `ai_reasons` 필드 추가; `daily-ai-trade-plan --max-order-value` 포지션 사이징
- `execute-last-approved`는 legacy/복구용 유지

## 완료 ([긴급-MVP] Telegram 연결 검증)

- `telegram-test` CLI 추가: dry-run 기본, `--send` 시 `sendMessage`, `outputs/telegram_test_*.json` 기록
- `telegram-approval-request` / `telegram-approval-listen` 콘솔 안내 및 승인 메시지 최소화(종목 수·금액·plan·승인/중단 버튼)
- listen은 `getUpdates`·callback 검증·audit/state만 갱신하며 `execute-last-approved` 자동 호출 없음

## 완료 ([실전-50] Archive Viewer Freshness Source Column)

- `archive-viewer` entries에 `generated_at`/`generated_date`/`timezone`/`freshness_source`/`freshness_status`를 추가했다.
- JSON metadata(`generated_at` 우선)와 Markdown 상단 timestamp block, 없으면 mtime fallback으로 기준 소스를 구분한다.
- `ARCHIVE_VIEWER.html`에 생성 시각·기준 소스 컬럼과 Freshness 기준 요약, CSV/JSON/Markdown summary export를 확장했다.
- `report-index`·`open-dashboard`에 archive freshness source summary를 표시한다.
- read-only 로컬 metadata read만 수행한다. KIS, Telegram, live-approve, execute, 파일 삭제/이동은 없다.

## 완료 ([실전-49] Daily AI Workflow Timestamp Normalization)

- `time_utils.py`를 추가해 `now_kst()`, timezone-aware ISO, `stamp_daily_ai_payload()`를 제공한다.
- Daily AI plan/report/status, Telegram approval, execute-approved audit JSON에 `generated_at`/`generated_date`/`timezone`을 일관 기록한다.
- Markdown 상단에 생성 시각/기준 날짜/타임존을 명시한다.
- `daily_ai_freshness`는 `generated_at` 우선·`generated_date` 보조·mtime fallback 시 warning/source 표시를 한다.
- `daily-ai-status`, `safety-audit`, `REPORT_INDEX`, `open-dashboard`에 freshness source 표시를 추가했다.
- 로컬 파일 read/write만 수행한다. 외부 시간 API·KIS·Telegram·execute 호출은 없다.

## 완료 ([실전-48] Daily AI Workflow Freshness Validation)

- `daily_ai_freshness.py`를 추가해 plan/latest order plan/approval/execution/report/status freshness를 Asia/Seoul 기준 날짜·max age hours로 검증한다.
- `daily-ai-status`와 `safety-audit`에 `--freshness-date YYYY-MM-DD` 옵션을 추가했다. 기본은 Asia/Seoul 오늘이다.
- `daily_ai_status_reader`가 freshness를 반영해 next_action을 계산하고 stale plan이면 `daily-ai-trade-plan`을 권장한다.
- `execute-last-approved`는 stale plan 또는 stale `live_order_plan_ai_latest.json`을 차단하고 `execute_approved_audit_*.json`에 stale reason을 기록한다.
- `report-index` / `open-dashboard`에 freshness 표시(최신/오래됨/없음)를 추가했다.
- 로컬 파일 metadata/JSON timestamp read만 수행한다. KIS, Telegram, live-approve, execute, KIS POST, 외부 시간 API 호출은 없다.

## 완료 ([실전-47] Daily AI Workflow Dashboard Integration)

- `daily_ai_status_reader.py`를 추가해 `AI_DAILY_*`, Telegram approval, execute-approved 상태를 outputs 파일만으로 계산한다.
- `report-index`에 `AI 일일 매매 운영` 섹션과 `daily_ai_workflow` JSON 필드를 추가했다.
- `open-dashboard` 출력에 AI daily plan/report/status 및 latest AI order plan 링크를 추가했다.
- `archive-viewer`가 `AI_DAILY_*`와 `live_order_plan_ai_latest.json`을 별도 report type으로 분류한다.
- `safety-audit`가 daily AI workflow 누락 단계를 warning과 다음 명령으로 표시한다.
- 모든 통합은 읽기/표시 전용이며 KIS, Telegram API, live-approve, execute, KIS POST를 호출하지 않는다.

## 미완료 / 후속

- `risk-check --sync-first`
- dashboard optional dependency 분리 또는 Tk 포함 macOS 패키징 검토
- AI_CONTEXT template refinement / Overmind integration
- Archive Viewer anomaly summary
- AI recommendation validation 고도화: 리밸런싱 정책 반영
- dashboard archive index 고도화
- SELL·시장가·자동 반복·취소

## 다음 추천

1. [실전-44] AI recommendation rebalance schedule validation
2. [실전-44] AI_CONTEXT Overmind integration metadata export
3. dashboard optional dependency 분리 또는 Tk 포함 macOS 패키징 검토
