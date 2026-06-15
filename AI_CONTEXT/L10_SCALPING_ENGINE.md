# L10 단타엔진 — 전체 작동 방식 (처음 보는 사람용)

작성: 2026-06-15 · 코인(Upbit) 스캘핑 자동매매 엔진을 코드 안 보고도 파악하도록 정리.

---

## 0. "L10"이 뭔가 — 공격성 다이얼

엔진은 손잡이 **하나**(`DEEPSIGNAL_AGGRESSION` = 1~10)로 전체 공격성을 조절한다
(`deepsignal/risk/aggression.py`). 이 숫자 하나가 `apply_aggression()`에서 **수십 개 env
플래그**로 펼쳐져 모든 러너에 적용된다(틱마다 호출 → 재시작 없이 즉시 반영).

| 밴드 | 단계 | 성격 |
|------|------|------|
| 안전(safe) | 1~5 | 검증된 전략만, 보수적. EDGE_GATE 켜짐 |
| 위험(risky) | 6~8 | 트레일링 익절·레버리지 ETF 가동 |
| **청산가능(liquidation_possible)** | **9~10** | **EDGE_GATE 해제 = 미검증도 베팅, 풀투입** |

**L10 = "풀투입·안전최소·청산 가능", 코드 표기 예상 MDD −85%.** 사실상 모든 게이트를 끈 상태.

---

## 1. 전체 파이프라인 (1틱마다 반복, L10은 ~15초 간격)

```
 ① 스캔        업비트 KRW 전 종목(거래대금 상위) — L10은 메이저 ~30종 밖 급등 알트까지
   ↓
 ② 점수계산    종목별 technical_score(-100~+100) → final_score
   ↓
 ③ 매수게이트   점수 ≥ 기준 / 변동률·RSI·스프레드·호가벽 / ML P(win) / 악재뉴스
   ↓
 ④ 사이징      Kelly 공식 × 포지션배수 × 동적 현금캡
   ↓
 ⑤ 매수실행    호가 맨앞 지정가 → 폴링 → 미체결 시 취소·재호가 1회
   ↓
 ⑥ 청산관리    보유분 매틱 평가: 손절/AI/트레일링/타임스톱/부분익절
```
오케스트레이션: `deepsignal/crypto_trading/runner/auto_runner.py` `_run_crypto_auto_tick_body`.

---

## 2. 점수 계산식 (`signal/scorer.py: compute_crypto_technical_score`)

**technical_score** (−100~+100, 기본 35점에서 가감):
```
score = 35 (baseline)
      + clamp(24h변동률% × 12, -30, +30)        ← 모멘텀
      + RSI보정: ≤30 +10 / ≥70 -10 / ≥50 +5
      + 거래량비율: ≥0.8 → +min(15,(vr-0.5)×10) / else -10
      + ATR>12% → -8                              ← 고변동성 감점
      + 일봉 골든크로스 +10 / EMA50>200 +6 / 데드크로스 -10 / below -6
      → clamp(-100, +100)
```

**final_score** = technical·뉴스·거시 가중합 (0.6 / 0.2 / 0.2, 없는 항목은 제외 후 재정규화).
`signal_scorer.score_final()` 공용 사용(주식과 동일).

**실시간 블렌딩**: Binance WS 스트림 피처가 있으면 스캘핑 점수를 합친다.
```
final = 일봉기반 × 0.5 + 스캘핑점수(0~100을 -100~+100으로 정규화) × 0.5
```
(`signal/scalping_scorer.compute_scalping_score`)

---

## 3. L10이 바꾸는 핵심 수치 (게이트가 거의 다 열림)

| 파라미터 | 기본(L1~) | **L10** | env 키 |
|----------|-----------|---------|--------|
| 매수 점수 기준 | 45점 | **15점** | CRYPTO_MIN_FINAL_SCORE |
| ML P(win) 게이트 | 0.55 | **0.0** | CRYPTO_EXEC_MIN_WIN_PROB |
| EDGE_GATE | 켜짐 | **꺼짐** | DEEPSIGNAL_ENFORCE_EDGE_GATE |
| 미검증 코인매수 | 차단 | **허용** | DEEPSIGNAL_ALLOW_UNVERIFIED_CRYPTO_BUY |
| 추격 변동률 캡 | +8% | **+60%** | CRYPTO_MAX_CHANGE_RATE |
| RSI 캡 | 90 | **100** | CRYPTO_MAX_RSI |
| 스프레드 허용 | 0.25% | **0.8%** | CRYPTO_MAX_SPREAD_PCT |
| 호가벽(bid≥ask×) | 1.0 | **0.3** | CRYPTO_MIN_BID_ASK_RATIO |
| 포지션 배수 | 0.4~1.0 | **2.0** | DEEPSIGNAL_POSITION_MULT |
| 일일 손실한도 | ×0.5 | **×999** | DEEPSIGNAL_MAX_DAILY_LOSS_KRW |
| 일일 매수캡 | 30만/5종목 | **무제한(0)** | CRYPTO_MAX_BUY_KRW_PER_DAY 등 |
| 재매수 쿨다운 | 20분 | **3분** | CRYPTO_REBUY_COOLDOWN_MINUTES |
| 공격적 체결 | off | **on(+0.5%)** | CRYPTO_AGGRESSIVE_FILL |
| 손절 최대깊이 | -3.0% | **-2.0%** | CRYPTO_SL_PCT_MIN |
| 트레일링 폭 | 0.8% | **2.5%** | CRYPTO_TRAILING_STOP_PCT |
| 타임스톱 | 5분 | **15분** | CRYPTO_TIME_STOP_MINUTES |
| 추세추종 ETF 배분 | 30만 | **0**(현금을 단타에 양보) | REGIME_TREND_ALLOC_KRW |

요지: **L10은 "거의 다 사고, 손실은 짧게(-2%)·이익은 길게(트레일 2.5%) 달리려는" 세팅.**

---

## 4. 사이징 — Kelly 공식 (`engine.py: kelly_fraction / kelly_order_krw`)

```
f = (P·b − (1−P)) / b          P = ML승률, b = TP/SL 비율(≈2.0/1.5 = 1.33)
주문액 = min(플랜금액, 총자산 × clamp(f, 0.01, 0.05))
```
켈리로 베팅 비율을 산출하고 1~5% 범위로 클램프. **L10은 P(win) 게이트가 0이라 ML이 사실상
무력** → 켈리도 보수적 하한(1%)에 머무는 경우가 많음. 이후 동적 현금캡(`risk/sizing.py
compute_max_order_krw`)으로 단일종목/단일주문/유동 상한까지 추가 제한.

---

## 5. 매수 실행 순서 (`engine.py: execute_buy`)

1. **ML P(win) 게이트** — `p_win ≥ buy_min_win_prob` (L10: 0 → 통과)
2. **악재 뉴스 차단** — LLM 감성 캐시 risk=block이면 차단(기본 off, fail-open)
3. **Kelly 사이징** — 위 4번
4. **호가 게이트**(`check_orderbook_for_buy`) — 스프레드 ≤ 한도, 매수/매도 잔량비 ≥ 기준,
   동적 스프레드 게이트(`spread_gate`)도 관측·적용
5. **지정가 산출** — mid 또는 bid+1틱. L10 공격적 체결이면 best ask 위 +0.5% 추격
6. **체결품질 사전평가**(`evaluate_pre_trade`) → 지정가 주문 → 폴링(타임아웃 10초) →
   미체결 시 **취소 후 재호가 1회**(limit_retry_max=1)

---

## 6. 청산 로직 (`engine.py: evaluate_exit`, 위→아래 순서로 먼저 맞는 것 발화)

`scan_dynamic_exit_holdings`가 **실제 업비트 보유분 전체**를 매틱 평가한다.

| 순위 | 트리거 | 조건 | 비고 |
|------|--------|------|------|
| 6(최우선) | **stop_loss** | pnl ≤ stop_loss_pct(-1.5%) | **A2 신규**: 브로커 pnl이라 고아 포지션도 청산 |
| 5 | ai_stop | P(win) < sell_ai_stop(L10:0) & 최소보유 후 | ML 모델 비면 무력 |
| 4 | trailing_stop | 현재가 < 고점×(1−2.5%) | L10 트레일 2.5% |
| 3 | time_stop | 보유 ≥ 15분 AND \|pnl\| ≤ 0.5% | **보합만** 청산(손실 종목 안 잡힘) |
| 1 | **max_hold** | 보유 ≥ CRYPTO_MAX_HOLD_MINUTES | **A5 신규**(기본 0=off): 손익무관 강제청산 |
| 2 | partial_take_profit | pnl ≥ 1.2% | 50% 부분익절 |
(괄호 안 숫자는 여러 종목 중 실행 우선순위. 손절이 최우선.)

매도 지정가는 `round_crypto_limit_price`로 업비트 호가단위 정렬(국내주식 A1의 코인판은 이미 존재).

---

## 7. 안전장치 (다이얼과 무관하게 항상)

- **EDGE_GATE**(`risk/edge_gate.py`): 검증된 엣지 전략만 신규매수 허용. 현재 코인 deploy=false → **하드차단**.
- **regime_gate**(`risk/regime_gate.py`, DM6 신규): S&P500 200일선 risk-off 시 신규 롱 차단.
- **TRADING_HALT**(`risk/trading_halt.py`): 일일 실현손실 한도 초과 시 자동 kill-switch.
- 오버트레이딩 가드(`crypto_overtrading_guards`): 재매수·시간당·재진입 쿨다운, 일일 캡.

---

## 8. ⚠️ 정직한 진단 (이 엔진의 실체)

코드는 정교하지만, 2026-06 세션의 3중 검증 결과:
- **IC 분해**: 점수 ↔ 미래수익 무상관(코인 IC ≤ 0.05). [[project-subscore-ic-analysis]]
- **캘리브레이션**: 전 점수버킷 수수료차감 net −0.20%(`scripts/calibrate_kgsqs.py`).
- **실거래**: 청산 38건 승률 28.9%, 순손익 −2,769원. [[project-live-execution-bugs]]

> **L10은 모든 게이트(EDGE_GATE·ML·점수기준·과열캡)를 꺼서 "거의 다 사는" 상태**다. 코드
> 자체가 이 밴드를 "도박/청산가능(MDD −85%)"로 표기한다. 정교한 사이징·체결 로직 위에
> **검증된 알파가 없어서**, 빠르고 정밀하게 손실을 내는 구조. 그래서 EDGE_GATE가 신규매수를
> 막고 있는 현재 상태가 자본보호상 맞다. 알파가 검증되는 날 EDGE_GATE가 자동으로 열린다.

**How to apply:** L10 단타엔진을 켜기 전, 위 §8 검증부터 통과(EDGE_GATE deploy=true)해야 한다.
파라미터 튜닝(점수식·사이징)은 알파 부재를 못 고친다 — 엣지가 먼저.
