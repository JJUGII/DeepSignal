# DeepSignal — 실전 자동매매 준비도 감사

> 작성일: 2026-06-01
> 관점: 안전 우선 프레이밍 배제, **"실전 라이브 자동매매로서 빠진 기능·위험 구멍"** 식별
> 방법: 6개 서브시스템 병렬 코드 감사 (KIS주문 / 리스크 / 코인 / 시그널·ML / 실시간데이터 / 운영). 모든 항목 코드 직접 확인, `파일:라인` 근거 기반.

---

## 🔴 0. 가장 중요한 현실 (감사 시점 상태)

**코인 시스템은 검토 단계가 아니라 이미 무승인·완전자율로 실거래 중이며, 손실 중이다.**

근거 (`outputs/CRYPTO_AUTO_RUNNER_STATE.json`, `.env`):
- `CRYPTO_PAPER_MODE=false`, `UPBIT_DRY_RUN=false`, `CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL=true`, Upbit 키 주입 → 라이브 무승인
- 2026-06-01 당일 실주문 **15건 / 252,784원** 집행
- 튜닝 기록: `buy_win_rate: 0.0` (27건 중 승 0), `sell_avg_return: -1.35%`
- `outputs/models/` **비어 있음** → ML 게이트가 `status="skipped"`로 전부 통과 (`ml/gate.py:179`) → 미검증 룰 점수만으로 매매
- "14일 unlock"은 코드상 강제 안 됨 (`risk/paper_state.py`는 정보용). `CRYPTO_PAPER_MODE` 한 줄로 즉시 라이브 (이미 그 상태)

→ "ML 자동매매"라고 켜져 있으나 **실제로는 ML이 꺼진 채, 일일 손실 한도 없이 가동 중.**

---

## 🚨 P0 — 자본 직접 위험 (실전 재개 전 필수)

| # | 빠진 기능 | 현황 / 근거 |
|---|---|---|
| 1 | **일일 손실 한도 / Kill Switch** | `daily_loss`·`max_daily_loss`·서킷브레이커 코드 전무. 연속 손절·급락장에서 자율 정지 수단 없음 |
| 2 | **전역 긴급정지** | 모든 러너·auto_sell·auto_execute를 한 번에 멈추는 단일 스위치/파일 플래그 없음 |
| 3 | **ML 게이트 실작동** | `outputs/models/` 비어 있음 + `CRYPTO_ML_BUY_GATE_STRICT` 미설정 → 무검증 통과 (`ml/gate.py:179`) |
| 4 | **일일 매수 캡 기본 활성** | `max_buy_krw_per_day=0`, `max_distinct_buy_markets_per_day=0` = OFF (`analysis_conditions.py:187-188`). 기본설정에서 무제한 |
| 5 | **실주문 가격이 실시간 아님** | KIS LIMIT가가 yfinance 일봉 종가 (`database.py:601`→`order_plan.py:124`→`order_executor.py:77`→`kis_broker.py:508`). 실시간 호가(H0STASP0) 수집해놓고 주문에 미연결 → 갭 종목 미체결/오체결 |

---

## 🟠 P1 — 실행 신뢰성

| # | 빠진 기능 | 근거 |
|---|---|---|
| 6 | **KIS 미체결 주문 취소(`cancel_order`) 전무** | `kis_broker.py` 취소 API 호출 0건 (취소는 Upbit만). 지정가 안 걸리면 종일 방치 |
| 7 | **시장가·재호가 없음 → 손절 미체결** | 손절 SELL이 `current_price` 지정가 (`auto_sell_executor.py:259`). 급락 중 미체결 → 손절 실패. `ORD_DVSN="00"` 하드코딩 (`kis_broker.py:506`) |
| 8 | **체결 확인 폴링 루프 없음 (KIS)** | 주문 후 체결 대기·타임아웃·부분체결 후속 없음. `fill_tracker`는 사후 조회용 (크립토엔 `follow_up_order_fill` 존재) |
| 9 | **stale 데이터가 매매 안 막음** | `stream_stale_alert`는 Telegram 알림만. 끊겨도 REST 폴백으로 낡은 데이터 매수 (`auto_runner.py:173` 기록만). KIS 스트림엔 stale 감지 모듈 자체가 없음 |
| 10 | **DB/상태파일 동시성 무방비** | `busy_timeout`/WAL 설정 0건 (`database.py:51-58`). crypto 4스레드가 상태 JSON 락 없이 read-modify-write (`ws_runner.py`) + 비원자적 `write_text` (`auto_runner.py:76`) → **일일 카운터 유실로 한도 우회** |

---

## 🟡 P2 — 견고성·모델 품질

| # | 항목 | 근거 |
|---|---|---|
| 11 | 매수 이력 미기록 → 중복가드 무력 | `save_real_order_history`(BUY) 호출처 0건 → `check_duplicate_order_risk`의 recent_orders가 BUY엔 항상 빈값. 러너 재시작/plan 재실행 시 중복 매수 가능 |
| 12 | 러너 tick 예외 격리 없음 | `tick_runner` catch-all 부재 (`daily_ai_auto_runner.py:421`). 일시 예외 1회가 루프 크래시 → crash-loop(ThrottleInterval 미설정), 운영자 통보 없음 |
| 13 | 해외주식·자동매도가 order_guard 우회 | `overseas_auto_execute.py:132`, `auto_sell_executor.py:310`이 가드 없이 직접 주문. 상태 문자열 불일치(`SUBMITTED` vs `KIS_ORDER_SUBMITTED`) |
| 14 | 백테스트 비현실 (주식) | `COMMISSION_RATE=0`·`SLIPPAGE_BPS=0`·익일종가 체결 (`backtest_engine.py:69-71,232`). 양의 수익이 실전선 음수 가능 |
| 15 | 가중치 미검증 + 옵티마이저 과적합 | 0.6/0.2/0.2, K-GSQS/GSQS 전부 수작업 상수. `kstock_weight_optimizer.py:120-180`는 in-sample SLSQP 1회 (워크포워드/OOS 없음), `improvement>=0`면 자동적용 |
| 16 | 국면 적응 없음 | `macro_regime`(risk_on/neutral/risk_off)은 라벨·reason 문자열일 뿐 전략/임계값/가중치 안 바꿈 |
| 17 | 한국어 감성 = 키워드 매칭 | `sentiment_analyzer.py:9-60` 영11+한10 substring. 부정어·문맥·종목귀속 없음. news 가중 0.2가 잘못된 신호 주입 가능 |
| 18 | 주기적 헬스체크 없음 | 헬스체크 plist가 `RunAtLoad`만 (`StartInterval` 없음). 24/7 중 죽으면 다음 로그인까지 kickstart 안 됨 |
| 19 | 데이터 절대 부족 | 실거래 27건(<30 배포게이트), bars 대부분 심볼 6일치 (BTC/ETH만 ~66일). P(win)·옵티마이저 통계 신뢰 불가 |
| 20 | 틱당 매도 1건 한계 (코인) | `build_sell_recommendation`이 틱 전체에서 단 1개 sell rec 반환 (`recommendation.py:338`). 급락장 동시 손절 시 청산 지연 |

---

## ✅ 잘 돼 있는 것 (실전 수준, 추가 작업 불필요)

- **주문 진입 가드**: `order_guard`·`execution_guard`·삼중확인(`KIS_ENV=live`+`--allow-live-env`+`--final-confirm`)·금액한도 = 기관 수준
- **Telegram 승인 → 자동 실행 연결**: 코드상 완결 (`auto_execute.py:165-209`). "수동 터미널 입력 병목"은 존재하지 않음 (메모리 기록이 낡음)
- **KIS 실시간 스트림 + K-GSQS**: 실재하고 신호 DB까지 연결 (`kis_stream/signal_bridge.py:142` → `upsert_kgsqs_signal`)
- **Binance WS 견고성**: 자동 재연결·지수백오프·상태복원·delta fetch (`binance_stream/pipeline.py:581`)
- **ML 검증 인프라 설계**: no-lookahead·TimeSeriesSplit(gap=10)·과적합 게이트(val_sharpe<0.5×train)·실거래 라벨·슬리피지 모델 (단, 데이터 부족+모델 미배포로 미가동)
- **자동매도 모듈 존재**: ATR 동적 TP/SL → 실 LIMIT SELL, 러너 연결 (`auto_sell_executor.py`, `KIS_AUTO_SELL_*` 게이트 기본 OFF)
- **감사추적**: plan SHA256·audit JSON·launchd KeepAlive 재시작·`#`경로 심볼릭 우회·세션/장외 가드

> 참고: 메모리의 "SELL/BUY-only 미구현"은 낡은 정보. 브로커 계층엔 SELL TR 구현됨(`kis_broker.py:416`), 자동매도 모듈도 연결됨. 진짜 빈칸은 "승인형 SELL plan"·시장가·취소.

---

## 권고 실행 순서

**즉시 (오늘)**
1. 라이브가 지고 있으므로 — `CRYPTO_PAPER_MODE=true` 또는 `CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL=false`로 멈추고 아래 채운 뒤 재개 결정
2. ML 모델 배포(`crypto-train-lgbm`) 또는 `CRYPTO_ML_BUY_GATE_STRICT=true` (모델 없으면 매수 차단)

**P0 (실전 재개 전 필수)**
3. 일일 손실 한도 kill-switch + 전역 긴급정지 (`outputs/TRADING_HALT` 파일 플래그를 모든 tick 시작 시 체크)
4. 일일 매수 캡 기본값을 보수값으로 (`analysis_conditions.py:187-188`)
5. KIS 실주문 가격을 실시간 호가로 교체 + 실행 가드에 가격 괴리(나이/괴리%) 검증 추가

**P1**
6. KIS `cancel_order`(`order-rvsecncl`, TR `TTTC0803U`) + 손절 미체결 방어(시장가 또는 재호가)
7. stale 데이터 → 거래 중단 게이트 (alert→block 승격)
8. DB `WAL`+`busy_timeout`, 상태파일 원자적 쓰기(tmp+`os.replace`)+스레드 락

**P2**
9. 매수 이력 기록 연결(중복가드 실효화), tick 예외 격리+`ThrottleInterval`
10. 주식 백테스트 실비용 주입, 옵티마이저 워크포워드 전환, 주기적 헬스체크

---

## 서브시스템별 상세 (감사 원본 요약)

### KIS 주식 주문·체결
- BUY 실주문 작동(`kis_broker.py:522,584`), SELL은 plan 경로에서 차단되고 auto_sell/overseas만 발주
- 빠짐: 취소API·재호가·시장가·체결폴링·부분체결후속·매수이력기록
- 구멍: 매수이력 미기록(중복가드 무력), 해외경로 가드우회, 손절 지정가 미체결, 전송-후-타임아웃 시 중복접수

### 리스크/자금관리
- 있음: 점수기반 사이징(Kelly 아님), 동적 TP/SL 연결, 진입가드, 오버트레이딩 가드
- 빠짐: 일일손실한도·계좌MDD halt·전역긴급정지·총익스포저강제·섹터/상관 라이브적용. `risk/risk_manager.py`는 빈 stub
- 구멍: 손실누적 무방어, 일일캡 기본OFF, equity누락 시 집중도 silent skip

### 코인 (Upbit)
- 있음: 실주문·매도트리거·재호가/취소(WS러너 한정)·과매매가드·상태영속
- 무력화: ML게이트(모델없음), stale미차단, 일일캡 OFF
- 구멍: 라이브+무승인+ML무력 동시, 승률0%인데 매매지속, time_stop이 본전포지션 강제청산(수수료잠식)

### 시그널·ML
- 설계 양호(검증 인프라), 실전 미가동(모델0·데이터27건)
- 약점: 가중치 미검증 수작업, 옵티마이저 in-sample 과적합, 주식백테스트 비용0, 감성 키워드매칭, 국면적응 없음

### 실시간 데이터
- Binance/KIS 스트림 실재·견고. 단 **실주문 가격이 yfinance 일봉종가로 산정되어 실시간과 단절**(치명)
- 빠짐: 앱레벨 워치독, KIS stale감지, 가격나이 검사, 실행직전 가격재조회

### 운영·자동화
- 강점: launchd KeepAlive, Telegram승인→자동실행, 감사추적, 세션가드
- P0구멍: DB동시성(WAL없음), crypto 상태파일 4스레드 레이스(한도우회), tick예외 미격리(crash-loop)
- 빠짐: 크래시 운영자알림, 주기적헬스체크, 휴일캘린더 자동화, 보조알림채널
