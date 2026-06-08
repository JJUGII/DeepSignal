# DM1 — Regime 3-상태 기계 (롱/현금/인버스) 설계

작성: 2026-06-05 · 분석엔진 담당 · 목적: **하락장에서도 수익을 내는 분석엔진**

## 1. 배경 — 왜 이것만이 실현 경로인가

- 엣지 리서치 결론: 3자산군·2엔진(technical_v1·K-GSQS) 모두 **수수료 후 검증 가능한 알파 없음**. 유일하게 robust한 엣지는 **S&P500 200일선 추세추종**(98년 OOS Sharpe 0.73 vs B&H 0.42, MDD −51% vs −86%, 생존편향 없음).
- IC 분해(2026-06-05): K-GSQS 총점은 *반(反)예측적*, 단기 모멘텀은 부호 오류. 단타 재설계로는 하락장 수익 불가.
- 현 `regime_trend`는 `{롱지수, 현금}` 2-상태 → 하락장에선 **현금(=덜 잃음)이 최선**. "하락으로 번다"는 목적 미달.
- 브로커 제약: Upbit 현물=숏 불가(하락수익 구조적 불가). KIS 공매도=리테일 불가. **유일한 합성 숏 = 인버스 ETF 롱**.

→ **검증된 200일선 엣지를 `{롱, 현금, 인버스}` 3-상태로 확장**. 신규 알파 발명이 아니라 기존 엣지의 대칭 확장.

## 2. 핵심 난점 (반드시 선반영)

### 2-1. 비대칭 리스크 — 숏 레그는 휩쏘가 실손실
- 롱/현금 휩쏘 = *기회비용*만. 인버스 휩쏘 = **실손실**(인버스 ETF 변동성 붕괴 + 반등 손실).
- ∴ **숏 진입 임계는 롱/현금보다 엄격해야** 한다(비대칭 밴드 + 지속성 + 기울기).

### 2-2. 기초자산 불일치 (최대 미결정)
- 신호는 **S&P500**. 그러나 한국 상장 1X 인버스의 주력 유동성은 **KOSPI200**(252670 KODEX 인버스) — *다른 지수*.
- 선택지:
  - **(A) 기초자산 일치 — 권장**: 신호 S&P500 → KIS 해외로 **SH**(ProShares Short S&P500, 1X) 거래. 롱 레그도 본래 S&P500 신호. 일관성 최고. 단 해외 order_guard/가격괴리 경로 미연결(엣지연구 메모: 해외 가드 미완).
  - **(B) 한국 인버스 + 별도 KOSPI 신호**: 252670 거래하되 **KOSPI200 200일선 신호를 따로 산출**. 기존 국내 가드 재사용. 단 KOSPI 레짐 엣지는 별도 OOS 검증 필요.
- ⚠️ 절대 금지: S&P500 신호로 KOSPI 인버스를 거래(지수 불일치 = 베이시스 리스크).
- **2X/곱버스(곱버스 251340 등) 제외**: 일일 리밸런싱 변동성 붕괴로 추세추종 호라이즌에 부적합.

### 2-3. 숏 레그는 미검증 가설
- 엣지연구는 **롱/현금만** 검증. "200일선 하회 시 인버스 보유가 돈이 되는가"는 **별개 가설** — 베어마켓 랠리에서 피흘릴 수 있음.
- ∴ 숏 레그는 **자체 EDGE_GATE 키 `regime_trend_short_sp500`**로 분리, 독립적으로 연속 검증(persist_runs=3) 통과 전엔 **닫아둠**. 롱 레그 deploy와 무관하게.

## 3. 상태 기계 정의

```
지표: c = 지수 종가, s = SMA200, slope = SMA200 기울기(예: s_today − s_20일전), days_below = 연속 하회일수

LONG_ENTRY :  c > s
SHORT_ENTRY:  c < s·(1 − band)  AND  slope < 0  AND  days_below ≥ persist_days   (예: band=2%, persist=10)
중간지대   :  위 둘 다 아님 → CASH

전이(히스테리시스·최소보유 적용):
  현금/숏 →(LONG_ENTRY)→ 롱
  롱/현금 →(SHORT_ENTRY)→ 숏
  롱      →(c ≤ s)→ 현금        # 롱 청산은 기존대로 즉시(보유 보호)
  숏      →(c ≥ s)→ 현금        # 숏 청산도 즉시(반등 보호)
  min_hold_days(예 3): 전이 직후 N일 재전이 금지 → 채터링 억제
```

- **숏 청산은 게이트·halt 무관 즉시 허용**(롱 청산과 동일 원칙: 포지션 보호 우선).
- **숏 진입만** 3중 게이트: `regime_trend_short_sp500` deploy + TRADING_HALT 아님 + REGIME_TREND_LIVE.

## 4. 구현 계획 (기존 코드 재사용)

`deepsignal/live_trading/regime_trend.py` 확장:
1. `compute_trend_signal` → `slope`, `days_below` 반환 추가(이미 closes 배열 보유).
2. `RegimeState = Literal["LONG","CASH","SHORT"]`; `decide_regime_trend`를 2-상태→3-상태로. `would_order`는 숏 진입 시 short-gate 참조.
3. `regime_trend_short_etf()` env(`REGIME_TREND_SHORT_ETF`, 기본 미설정=숏 비활성). 설정 전엔 SHORT_ENTRY여도 CASH로 폴백(안전 기본 닫힘).
4. `execute_regime_trend`에 SHORT 진입/청산 분기(ENTER/EXIT 패턴 복제, side=BUY 인버스 / SELL 인버스).
5. `edge_gate.LIVE_STRATEGY_MAP`에 `regime_trend_short` → `regime_trend_short_sp500` 추가. `scripts/eval_macro_regime.py`에 숏 레그 평가 추가 → edge_monitor가 매일 추적.
6. 상태파일 `REGIME_TREND_STATE.json`에 `mode: LONG|CASH|SHORT` 필드.

## 5. 검증 게이트 (배포 전 필수)

1. **백테스트(선행)**: `eval_macro_regime.py`로 98년 S&P500 일봉에 3-상태 시뮬. 측정: 숏 레그가 (a) B&H·롱전용 대비 Sharpe 개선, (b) MDD 악화 없음, (c) 거래비용·인버스 decay 반영 후 양(+). **여기서 실패하면 숏 레그 폐기** — 현금 유지가 정답.
2. **persist 검증**: EDGE_GATE `regime_trend_short_sp500`이 연속 3회 통과해야 deploy=true. 매일 자라는 데이터로 p-hacking 방지.
3. **dry-run**: 실주문 전 ENTER/EXIT 미리보기로 수량·가격·가드 확인(롱 레그와 동일 패턴).

## 6. 파라미터 (초기·env 오버라이드)

| env | 기본 | 의미 |
|-----|------|------|
| `REGIME_TREND_SHORT_ETF` | (미설정) | 인버스 ETF. 미설정=숏 비활성(CASH 폴백) |
| `REGIME_TREND_SHORT_BAND` | 0.02 | 숏 진입 SMA200 하향 밴드(2%) |
| `REGIME_TREND_SHORT_PERSIST_DAYS` | 10 | 연속 하회 요구일 |
| `REGIME_TREND_SHORT_MIN_HOLD_DAYS` | 3 | 전이 후 최소보유(채터링 억제) |
| `REGIME_TREND_SHORT_ALLOC_KRW` | 롱의 0.5배 | 숏 배분(decay 고려 축소) |

## 7. 정직한 한계

- **코인은 이 설계로 못 구함**: Upbit 현물뿐 → 하락수익 구조적 불가. 베어장 임무=자본보존(EDGE_GATE 매수차단 유지). 코인 하락수익은 *선물 거래소 도입*이라는 별개 의사결정.
- 숏 레그 백테스트가 음(−)이면 **이 설계의 결론은 "하락장엔 현금이 정답"** 이 되고, 목적은 "안 잃기"로 재정의됨. 데이터가 답을 정한다.
