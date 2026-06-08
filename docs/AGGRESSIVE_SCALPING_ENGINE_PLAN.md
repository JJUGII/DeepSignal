# DeepSignal 고도화 단타 엔진 기획서

> 작성일: 2026-05-31  
> 목적: 현행 GSQS 기반 반자동 단타 엔진을 수익 최적화 중심의 완전 자동 고속 단타 엔진으로 전환

---

## 1. 현황 요약

### 현재 엔진 구조
```
신호 감지 (5분 주기)
  → 텔레그램 승인 요청
    → 사용자 클릭
      → 실행 엔진 스프레드/품질 체크
        → 업비트 지정가 주문
```

### 핵심 병목 & 한계

| 항목 | 현황 | 문제 |
|------|------|------|
| 신호→체결 레이턴시 | 30초~수분 | 단타 타이밍 소실 |
| 스프레드 기준 | 0.15% 고정 | 코인별 특성 무시, 과잉 차단 |
| 임계값 | 고정 (62~68pt) | 장세 무관 동일 기준 |
| Kelly 상한 | 5% 고정 | 고확신 신호에서도 소량 진입 |
| 피드백 루프 | 없음 | 실전 결과가 다음 판단에 미반영 |
| 피라미딩 | 미구현 | 수익 중 추가진입 불가 |

---

## 2. 목표

**단기 (4주):** 레이턴시 90% 제거, 스프레드 게이트 코인별 동적 적용  
**중기 (8주):** 레짐 연동 공격성 조절, 실전 피드백 루프 완성  
**장기 (12주):** 피라미딩, ML 기반 승률 예측, 완전 자동화  

### 목표 지표

| KPI | 현재 | 목표 |
|-----|------|------|
| 신호→체결 레이턴시 | ~60초 | < 5초 |
| 일 평균 체결 건수 | 8~12건 | 15~25건 |
| 평균 체결오차 (bps) | 5~15 | < 5 |
| 승률 (5분 기준) | ~54% | 58%+ |
| 코인당 평균 보유시간 | 30~90분 | 10~40분 |

---

## 3. 기능 설계

---

### Phase 1 — 패스트레인 자동 실행 `(1~2주)`

**배경**  
현재 구조는 신호→사람 승인→실행이라 단타에서 사실상 타이밍을 놓침. 승인이 필요한 것은 리스크 관리지 실행 속도를 희생할 이유가 없음.

**설계**

```
ws_runner 내부 fastlane 큐 추가
  ┌─ 신호 감지 즉시 →  P(win) ≥ 0.65 AND 스프레드 OK → 자동 실행 (< 3초)
  │                    텔레그램으로 사후 통보만
  │
  └─ P(win) 0.55~0.65  → 기존 승인 프로세스 유지
     P(win) < 0.55     → 패스
```

**자동 실행 조건 (AND 전부 충족)**
- `P(win) ≥ 0.65` (현재 모델 예측 승률)
- GSQS 점수 ≥ 72pt
- 스프레드 < 코인별 동적 기준 (Phase 2에서 완성)
- OvertradingGuard 통과 (rebuy cooldown 등 기존 보호 유지)
- 일일 자동 실행 한도: 최대 10건 (ENV로 제어)

**텔레그램 사후 알림 포맷**
```
⚡ [자동 체결] KRW-NEAR 매수
주문가: 3,684원 | 금액: ₩36,029
사유: P(win)=0.71, GSQS=74pt
✅ 승인 | ❌ 즉시 청산
```

**파일 변경**
- `runner/ws_runner.py` — `_fastlane_execute()` 메서드 추가
- `runner/auto_runner.py` — fastlane 임계값 ENV 설정
- `telegram/flow.py` — 사후 통보 메시지 포맷

---

### Phase 2 — 코인별 동적 스프레드 게이트 `(1주)`

**배경**  
현재 0.15% 단일 기준은 비트코인처럼 타이트한 코인을 불필요하게 막고, NEAR처럼 스프레드가 원래 넓은 코인도 같은 기준으로 판단함. 실제 로그 기준 정상 거래의 30~40%가 이 게이트에서 차단됨.

**설계**

```python
# 코인별 최근 24시간 스프레드 중앙값을 캐싱
# 기준: median_spread × 1.5 (평소보다 50% 이상 벌어졌을 때만 차단)

class DynamicSpreadGate:
    def __init__(self, output_dir):
        self.history: dict[str, deque] = {}   # market → 최근 1000개 스프레드
    
    def record(self, market: str, spread_pct: float) -> None:
        """ws_runner 틱마다 호가창 스프레드 기록"""
    
    def threshold(self, market: str) -> float:
        """코인별 동적 임계값 반환 (데이터 없으면 0.3% 기본값)"""
        median = statistics.median(self.history[market])
        return max(0.15, min(0.5, median * 1.5))
    
    def allowed(self, market: str, current_spread: float) -> tuple[bool, str]:
        thr = self.threshold(market)
        return current_spread <= thr, f"spread {current_spread:.3f}% vs thr {thr:.3f}%"
```

**파일 변경**
- `execution/engine.py` — `ExecutionEngineConfig.max_spread_pct` → `DynamicSpreadGate` 교체
- `runner/ws_runner.py` — 틱마다 스프레드 기록 추가

---

### Phase 3 — 레짐 연동 공격성 조절 `(1~2주)`

**배경**  
BULLISH 국면에서는 낮은 점수 신호도 수익으로 연결되는 경우가 많고, BEARISH에서는 높은 점수 신호도 손실이 잦음. 현재 기준은 이를 전혀 반영하지 않음.

**설계**

```python
@dataclass
class RegimeProfile:
    gsqs_threshold_delta: int     # 기준점수 ± 조정
    kelly_max_fraction: float     # Kelly 상한
    fastlane_min_pwin: float      # 자동실행 승률 기준
    trailing_stop_pct: float      # 트레일링 스탑

REGIME_PROFILES = {
    "STRONG_BULL":  RegimeProfile(delta=-8,  kelly=0.10, pwin=0.62, trail=1.2),
    "BULL":         RegimeProfile(delta=-4,  kelly=0.08, pwin=0.65, trail=1.0),
    "NEUTRAL":      RegimeProfile(delta=0,   kelly=0.05, pwin=0.68, trail=0.8),
    "BEAR":         RegimeProfile(delta=+5,  kelly=0.03, pwin=0.72, trail=0.6),
    "STRONG_BEAR":  RegimeProfile(delta=+10, kelly=0.02, pwin=0.99, trail=0.5),  # 사실상 동결
}
```

**레짐 판정 소스** (이미 있는 것 활용)
- `macro_status.active` → 매크로 이벤트 감지 중이면 BEAR 고정
- GSQS 전체 평균 점수 추세 (최근 10분 vs 30분)
- BTC 5분 수익률

**파일 변경**
- `execution/engine.py` — `RegimeProfile` 클래스 및 적용 로직
- `runner/auto_runner.py` — 레짐 프로파일 로딩
- `web_ui/server.py` — 현재 레짐 & 프로파일 API 노출

---

### Phase 4 — 실전 피드백 루프 `(2주)`

**배경**  
GSQS 가중치 최적화는 이미 있지만, 코인별 실전 체결 품질(오차, 체결시간, 실현손익)이 다음 신호 생성에 반영되지 않음.

**설계**

```
실전 체결 완료
  → OutcomeTracker.record(market, signal_features, realized_pnl, fill_quality)
    → 코인별 통계 누적 (승률, 평균 보유시간, 평균 체결오차)
      → 다음 신호 평가 시 보정 계수 적용
```

**보정 계수 예시**

```python
class MarketExecutionBias:
    """코인별 실전 체결 경험 기반 보정"""
    
    def pwin_adjustment(self, market: str) -> float:
        """실전 승률이 예측 승률보다 낮으면 감산"""
        predicted = self.avg_predicted_pwin(market)
        realized  = self.avg_realized_pwin(market)
        gap = realized - predicted
        return max(-0.10, min(0.05, gap * 0.5))
    
    def spread_bias(self, market: str) -> float:
        """실제 체결오차가 크면 기대수익 하향"""
        avg_slip_bps = self.avg_slippage_bps(market)
        return avg_slip_bps / 10_000.0  # bps → pct
```

**파일 변경**
- `execution/quality.py` — `OutcomeTracker` 클래스 신규
- `runner/auto_runner.py` — 체결 완료 후 `OutcomeTracker.record()` 호출
- `signal/scalping_scorer.py` — 보정 계수 반영

---

### Phase 5 — 다이나믹 TP/SL + 부분 익절 고도화 `(1주)`

**배경**  
현재 TP/SL은 ATR 기반이지만 진입 직후 고정됨. 실시간 변동성 변화, 현재 수익 구간에 따른 동적 조정이 없음.

**설계**

```
진입 후 포지션 모니터링 (10초 주기)

수익률 구간별 전략:
  -1.0% 이하    → 즉시 스탑 (stop_loss 트리거)
  -0.5% ~ 0%   → 트레일링 스탑 0.3% (타이트하게)
   0% ~ +0.5%  → 트레일링 스탑 0.5%
  +0.5% ~ +1%  → 부분 익절 30% 실행, 나머지 트레일링 0.8%
  +1% ~ +2%    → 부분 익절 50% 실행, 나머지 트레일링 1.2%
  +2% 이상      → 전량 익절 실행
```

**파일 변경**
- `runner/ws_runner.py` — `_build_sell_thresholds()` 구간별 로직 교체
- `execution/engine.py` — `partial_tp_fraction` 동적 계산

---

### Phase 6 — 피라미딩 (Add-on 전략) `(1~2주)`

**배경**  
이기고 있는 포지션에서 추가 진입하는 것이 단타 수익의 핵심 전술 중 하나. 현재 세팅(`max_add_on_buys=3`)은 있지만 어떤 조건에서 추가 진입할지 트리거가 없음.

**설계**

```
기존 포지션 수익률 ≥ +0.5%
  AND GSQS 점수 여전히 ≥ 65pt
  AND 방향성(추세) 서브스코어 ≥ 70pt
  AND 현재 시장이 매크로 이벤트 중이 아닐 것
  → 추가 매수 (원 포지션의 30~50% 금액)
  → 평균단가 재계산
  → TP/SL 리셋
```

**안전장치**
- 최대 피라미딩 횟수: 2회 (원 진입 포함 총 3레그)
- 누적 포지션이 포트폴리오의 15% 초과 시 차단
- 추가 진입 시마다 별도 감사 로그 기록

**파일 변경**
- `runner/ws_runner.py` — `_check_pyramid_opportunity()` 신규
- `risk/overtrading.py` — 피라미딩 전용 Guard 조건 추가

---

## 4. 우선순위 & 일정

```
Week 1-2  [Phase 1] 패스트레인 자동 실행
Week 2    [Phase 2] 동적 스프레드 게이트
Week 3-4  [Phase 3] 레짐 연동 공격성 조절
Week 4-5  [Phase 4] 실전 피드백 루프
Week 5    [Phase 5] 다이나믹 TP/SL
Week 6-7  [Phase 6] 피라미딩
```

**반드시 Phase 1 → 2 순서로** (스프레드 게이트 없이 자동 실행하면 안 됨)  
Phase 3~6은 병렬 가능.

---

## 5. 리스크 & 완화 방안

| 리스크 | 가능성 | 완화 방안 |
|--------|--------|-----------|
| 자동 실행 오작동으로 대량 손실 | 중 | 일일 자동 실행 한도, 즉시 청산 텔레그램 버튼 |
| 레짐 오판 (BULLISH인데 BEAR 처리) | 중 | 레짐 판정 3개 소스 AND 조건, 1시간 지연 적용 |
| 피라미딩 중 급반전 | 높음 | 2회 한도, 누적 포지션 15% 상한, 트레일링 스탑 타이트하게 |
| 스프레드 기록 없는 신규 코인 | 낮음 | 최소 24시간 기록 쌓인 코인만 자동 실행 허용 |
| 피드백 루프 데이터 부족 | 중 | 50건 미만 코인은 보정 적용 안 함 |

---

## 6. ENV 플래그 설계 (안전 스위치)

```env
# Phase 1 - 자동 실행
CRYPTO_FASTLANE_ENABLED=true
CRYPTO_FASTLANE_MAX_DAILY=10
CRYPTO_FASTLANE_MIN_PWIN=0.65
CRYPTO_FASTLANE_MIN_GSQS=72

# Phase 2 - 동적 스프레드
CRYPTO_DYNAMIC_SPREAD_ENABLED=true
CRYPTO_SPREAD_FALLBACK_PCT=0.30

# Phase 3 - 레짐 공격성
CRYPTO_REGIME_AGGRESSION_ENABLED=true

# Phase 4 - 피드백 루프
CRYPTO_OUTCOME_FEEDBACK_ENABLED=true
CRYPTO_FEEDBACK_MIN_SAMPLES=50

# Phase 5 - TP/SL 고도화
CRYPTO_DYNAMIC_TPSL_ENABLED=true

# Phase 6 - 피라미딩
CRYPTO_PYRAMID_ENABLED=false        # 초기 꺼두고 검증 후 활성화
CRYPTO_PYRAMID_MAX_LEGS=2
CRYPTO_PYRAMID_MAX_POSITION_PCT=15
```

---

## 7. 기대 효과 (보수적 추정)

현재 시스템 월 수익률 기준:
- 횡보장: ±0% (이미 수수료 이겨내기 힘듦)
- 상승장: +3~5%
- 강세장: +5~10%

개선 후 예상:
- 횡보장: +1~3% (자동 실행 + 타이밍 개선)
- 상승장: +6~12%
- 강세장: +10~20%

> ⚠️ 단, Phase 6 (피라미딩) 적용 시 하락장 손실도 비례해서 커짐.  
> 완전 자동화 전에 반드시 1~2주 페이퍼 트레이딩 검증 필수.

---

## 8. 검증 계획

각 Phase 배포 전:
1. **페이퍼 모드** (`DRY_RUN=true`) 48시간 실행, 예상 체결 vs 실제 시장가 비교
2. **소액 실전** (건당 1만원 한도) 1주일, 승률·오차 지표 확인
3. **정상 실전** 전환 (건당 3~5만원)

롤백 기준: 연속 5건 이상 손절, 또는 일일 손실 포트폴리오 3% 초과 시 자동 중단 + 텔레그램 알림.

---

*이 기획서는 현재 DeepSignal 코드베이스를 기반으로 작성됐으며, 각 Phase는 독립적으로 병합/롤백 가능하게 설계함.*
