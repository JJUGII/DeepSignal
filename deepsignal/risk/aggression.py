"""투자공격성 다이얼 (1~10) — 단일 손잡이로 엔진 전체 공격성 조절.

DEEPSIGNAL_AGGRESSION = N (1~10) 하나로 모든 파라미터를 계산한다.
숫자가 클수록 리스크↑ 이지만 수익 증폭 레버(레버리지·포지션·트레일링·피라미딩)도 함께 켜진다.

구간:
  1~5  안전 (빈도·종목수 증가, 검증된 전략만)
  6~8  위험 (6단계부터 익절 트레일링 전환 = 수익 달리기, 레버리지 가동)
  9~10 청산가능 (EDGE_GATE 완화 = 미검증 전략도 베팅, 풀 레버리지)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict


DEFAULT_LEVEL = 1
ENV_KEY = "DEEPSIGNAL_AGGRESSION"


@dataclass
class AggressionProfile:
    level: int
    band: str                 # safe | risky | liquidation_possible
    band_kr: str
    # 켜지는 전략
    crypto_auto: bool
    stock_auto: bool
    overseas_auto: bool
    fastlane: bool
    pyramiding: bool
    leverage_etf: bool
    inverse_etf: bool
    # 증폭/리스크 레버
    position_mult: float      # 포지션 크기 배수
    leverage_max: float       # 최대 레버리지 배율 (1~3)
    entry_threshold_mult: float  # 진입 문턱 배수 (낮을수록 자주 진입)
    take_profit_mode: str     # fixed | dynamic | trailing
    daily_loss_mult: float    # 일일 손실한도 배수 (클수록 더 버팀)
    runner_interval_mult: float  # 매매 주기 배수 (작을수록 빠름)
    edge_gate_enforced: bool  # False면 미검증 전략도 허용(9~10)
    est_mdd_pct: int          # 예상 최대낙폭(대략)
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


# 단계별 핵심 파라미터 테이블 (1~10)
_TABLE = {
    # 3개 시장(코인·국내·해외) 자동 + 패스트레인은 모든 단계 ON. 다이얼은 '공격성'만 조절.
    1:  dict(band="safe", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=False, leverage_etf=False, inverse_etf=False,
             position_mult=0.4, leverage_max=1.0, entry_threshold_mult=1.30, take_profit_mode="dynamic",
             daily_loss_mult=0.5, runner_interval_mult=1.0, edge_gate_enforced=True, est_mdd_pct=-12,
             note="전체 자동 · 최소 베팅·높은 문턱 (가장 보수적)"),
    2:  dict(band="safe", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=False, leverage_etf=False, inverse_etf=False,
             position_mult=0.6, leverage_max=1.0, entry_threshold_mult=1.20, take_profit_mode="fixed",
             daily_loss_mult=0.7, runner_interval_mult=1.0, edge_gate_enforced=True, est_mdd_pct=-16,
             note="전체 자동 · 소액·보수적"),
    3:  dict(band="safe", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=False, leverage_etf=False, inverse_etf=False,
             position_mult=0.8, leverage_max=1.0, entry_threshold_mult=1.10, take_profit_mode="fixed",
             daily_loss_mult=0.9, runner_interval_mult=0.9, edge_gate_enforced=True, est_mdd_pct=-20,
             note="전체 자동 · 표준 보수"),
    4:  dict(band="safe", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=False, leverage_etf=False, inverse_etf=False,
             position_mult=1.0, leverage_max=1.0, entry_threshold_mult=1.0, take_profit_mode="dynamic",
             daily_loss_mult=1.0, runner_interval_mult=0.8, edge_gate_enforced=True, est_mdd_pct=-25,
             note="전체 자동 · 표준"),
    5:  dict(band="safe", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=False, inverse_etf=False,
             position_mult=1.15, leverage_max=1.0, entry_threshold_mult=0.95, take_profit_mode="dynamic",
             daily_loss_mult=1.2, runner_interval_mult=0.6, edge_gate_enforced=True, est_mdd_pct=-30,
             note="전체 자동 · 적극(피라미딩·부분익절)"),
    6:  dict(band="risky", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=True, inverse_etf=False,
             position_mult=1.3, leverage_max=1.5, entry_threshold_mult=0.92, take_profit_mode="trailing",
             daily_loss_mult=1.5, runner_interval_mult=0.5, edge_gate_enforced=True, est_mdd_pct=-38,
             note="★수익 달리기(트레일링)+레버리지 1.5x"),
    7:  dict(band="risky", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=True, inverse_etf=False,
             position_mult=1.6, leverage_max=2.0, entry_threshold_mult=0.85, take_profit_mode="trailing",
             daily_loss_mult=2.0, runner_interval_mult=0.4, edge_gate_enforced=True, est_mdd_pct=-48,
             note="레버리지 2x·문턱↓·베팅↑"),
    8:  dict(band="risky", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=True, inverse_etf=True,
             position_mult=1.8, leverage_max=2.0, entry_threshold_mult=0.80, take_profit_mode="trailing",
             daily_loss_mult=3.0, runner_interval_mult=0.35, edge_gate_enforced=True, est_mdd_pct=-58,
             note="레버리지 주력·인버스 허용·공격적"),
    9:  dict(band="liquidation_possible", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=True, inverse_etf=True,
             position_mult=2.0, leverage_max=3.0, entry_threshold_mult=0.70, take_profit_mode="trailing",
             daily_loss_mult=6.0, runner_interval_mult=0.3, edge_gate_enforced=False, est_mdd_pct=-72,
             note="레버리지 3x·EDGE_GATE 완화(도박)"),
    10: dict(band="liquidation_possible", crypto_auto=True, stock_auto=True, overseas_auto=True, fastlane=True,
             pyramiding=True, leverage_etf=True, inverse_etf=True,
             position_mult=2.0, leverage_max=3.0, entry_threshold_mult=0.55, take_profit_mode="trailing",
             daily_loss_mult=999.0, runner_interval_mult=0.25, edge_gate_enforced=False, est_mdd_pct=-85,
             note="풀투입·안전최소·청산 가능"),
}
_BAND_KR = {"safe": "안전", "risky": "위험", "liquidation_possible": "청산 가능"}


def clamp_level(n) -> int:
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    return max(1, min(10, n))


def current_level() -> int:
    return clamp_level(os.environ.get(ENV_KEY, str(DEFAULT_LEVEL)))


def resolve(level: int | None = None) -> AggressionProfile:
    lvl = clamp_level(current_level() if level is None else level)
    t = _TABLE[lvl]
    return AggressionProfile(level=lvl, band=t["band"], band_kr=_BAND_KR[t["band"]], **{
        k: v for k, v in t.items() if k != "band"
    })


def summary_table() -> list[dict]:
    """UI용 1~10 전체 요약."""
    return [resolve(i).to_dict() for i in range(1, 11)]


# ── 러너 배선: 단계 → 기존 검증된 env 게이트로 변환 ──────────────────
_BASE: dict[str, str] = {}   # 곱셈 기준이 되는 원본 값 캐시


def _base(key: str, default: str) -> str:
    if key not in _BASE:
        _BASE[key] = os.environ.get(key, default)
    return _BASE[key]


def apply_aggression(level: int | None = None) -> AggressionProfile:
    """현재(또는 지정) 단계를 실제 엔진 env 플래그로 적용.

    각 러너가 tick마다 호출하면 다이얼 변경이 즉시 반영된다.
    1단계면 공격 옵션 전부 OFF(현재 안전 상태), 단계가 오를수록 켜진다.
    기존에 검증된 게이트(env)를 재사용 → 새 경로 없이 안전.
    """
    p = resolve(level)
    e = os.environ

    def sb(k: str, v: bool) -> None:
        e[k] = "true" if v else "false"

    # 시장별 무승인 자동
    sb("CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL", p.crypto_auto)
    sb("KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", p.stock_auto)
    sb("OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL", p.overseas_auto)
    # 부가 전략 토글
    sb("CRYPTO_FASTLANE_ENABLED", p.fastlane)
    sb("CRYPTO_PYRAMIDING_ENABLED", p.pyramiding)
    sb("REGIME_LEVERAGE_ENABLED", p.leverage_etf)
    sb("REGIME_INVERSE_ENABLED", p.inverse_etf)
    e["REGIME_MAX_LEVERAGE"] = str(p.leverage_max)
    # 엣지 게이트 (9~10단계만 완화)
    sb("DEEPSIGNAL_ENFORCE_EDGE_GATE", p.edge_gate_enforced)
    # 9~10(도박/청산가능)은 '미검증 엣지 코인 하드차단'·'ML fail-open 차단'도 해제.
    # (이 플래그를 안 켜면 ENFORCE_EDGE_GATE=false여도 코인 live BUY가 별도 게이트에서 막힘)
    # 1~8단계는 차단 유지(안전).
    sb("DEEPSIGNAL_ALLOW_UNVERIFIED_CRYPTO_BUY", not p.edge_gate_enforced)
    sb("DEEPSIGNAL_ALLOW_CRYPTO_ML_FAIL_OPEN", not p.edge_gate_enforced)
    # 9~10단계(도박): 추세추종(패시브 ETF)이 현금을 흡수하지 않게 신규 배분 0.
    # 공격적 단타(K-GSQS·인트라데이)가 현금을 쓰도록 양보. 1~8단계는 .env 기본 복원.
    base_rt_alloc = _base("REGIME_TREND_ALLOC_KRW", "300000") or "300000"
    e["REGIME_TREND_ALLOC_KRW"] = "0" if not p.edge_gate_enforced else str(base_rt_alloc)
    # 일일 손실 한도 = 기준 × 배수
    base_loss = float(_base("DEEPSIGNAL_MAX_DAILY_LOSS_KRW", "100000") or "100000")
    e["DEEPSIGNAL_MAX_DAILY_LOSS_KRW"] = str(int(base_loss * p.daily_loss_mult))
    # 사이징·문턱·익절모드 (소비측에서 읽음)
    e["DEEPSIGNAL_POSITION_MULT"] = str(p.position_mult)
    e["DEEPSIGNAL_ENTRY_THRESHOLD_MULT"] = str(p.entry_threshold_mult)
    e["DEEPSIGNAL_TP_MODE"] = p.take_profit_mode

    # ── 코인 ML 승률 게이트도 단계에 연동 ───────────────────────────
    # 낮은 단계=엄격(품질↑), 높은 단계=완화. 9~10(도박)은 ML 게이트 사실상 해제.
    base_ml = float(_base("CRYPTO_ML_BUY_THRESHOLD", "0.55") or "0.55")
    ml_thr = round(base_ml * p.entry_threshold_mult, 2)
    if not p.edge_gate_enforced:           # 9~10단계
        ml_thr = 0.12 if p.level == 9 else 0.05
        e["CRYPTO_GATE_MODE"] = "hybrid"   # 규칙 점수만으로도 매수 허용
        # 9~10단계: ML 게이트·앙상블 완전 해제.
        # (LGBM 모델이 모든 종목에 ~0을 뱉어 veto하는 상태라, 규칙점수로 거래)
        e["CRYPTO_ML_BUY_GATE"] = "false"
        e["CRYPTO_ML_ENSEMBLE"] = "false"
    e["CRYPTO_ML_BUY_THRESHOLD"] = str(ml_thr)
    e["CRYPTO_ML_HYBRID_THRESHOLD"] = str(ml_thr)
    # 실행엔진 자체 P(win) 게이트(기본 0.55)도 단계 연동 — 안 그러면 9~10에서도
    # 실행 직전 0.55 벽에 막힌다. 9~10은 사실상 해제(0.05).
    _exec_wp = round(0.55 * p.entry_threshold_mult, 2)
    if not p.edge_gate_enforced:
        _exec_wp = 0.05
    e["CRYPTO_EXEC_MIN_WIN_PROB"] = str(_exec_wp)
    # AI 매도 임계값은 매수 임계값보다 확실히 낮게(절반) — 사자마자 'AI 승률낮음'
    # 으로 즉시 청산되는 모순 방지. (매수 0.05 → 매도 0.02)
    e["CRYPTO_SELL_AI_STOP_PROB"] = str(round(min(_exec_wp * 0.5, 0.30), 3))
    # 주식(국내·해외) 매수 점수 기준도 단계 연동 (기본 60점 × 문턱배수)
    base_stock = float(_base("DEEPSIGNAL_STOCK_MIN_SCORE", "60") or "60")
    stock_min = round(base_stock * p.entry_threshold_mult, 1)
    if not p.edge_gate_enforced:           # 9~10: 사실상 다 매수
        stock_min = 30.0 if p.level == 9 else 20.0
    e["DEEPSIGNAL_STOCK_MIN_SCORE"] = str(stock_min)
    # 코인 매수 점수 기준도 단계 연동 (기본 45점 × 문턱배수). 낮은 단계=엄격, 높은 단계=완화.
    # 이게 없으면 10단계여도 45점 벽에 막혀 코인이 거의 안 사진다.
    base_cmin = float(_base("CRYPTO_MIN_FINAL_SCORE", "45") or "45")
    crypto_min = round(base_cmin * p.entry_threshold_mult, 1)
    if not p.edge_gate_enforced:           # 9~10단계
        crypto_min = 25.0 if p.level == 9 else 15.0
    e["CRYPTO_MIN_FINAL_SCORE"] = str(crypto_min)
    # 거래량(유동성) 게이트: 높은 단계일수록 완화 (9~10은 illiquid도 허용)
    base_vol = float(_base("CRYPTO_MIN_VOLUME_RATIO", "0.8") or "0.8")
    e["CRYPTO_MIN_VOLUME_RATIO"] = str(round(max(0.05, base_vol * p.entry_threshold_mult * (0.3 if not p.edge_gate_enforced else 1.0)), 2))

    # ── 코인 추격매수 캡(급등주 단타 허용) 단계 연동 ───────────────────
    # 기본은 "이미 +8% 급등한 종목은 추격 안 함 / RSI 90 과열 제외"로 보수적.
    # 단계가 오를수록 캡을 풀어 급등·과열 종목도 단타 대상에 포함한다.
    # 9~10단계는 SYRUP 같은 +20% 급등주도 진입 허용(고위험 추격).
    _chg_cap = {1: 0.08, 2: 0.08, 3: 0.08, 4: 0.08, 5: 0.08,
                6: 0.12, 7: 0.18, 8: 0.25, 9: 0.40, 10: 0.60}
    _rsi_cap = {1: 90.0, 2: 90.0, 3: 90.0, 4: 90.0, 5: 90.0,
                6: 92.0, 7: 94.0, 8: 96.0, 9: 98.0, 10: 100.0}
    e["CRYPTO_MAX_CHANGE_RATE"] = str(_chg_cap.get(p.level, 0.08))
    e["CRYPTO_MAX_RSI"] = str(_rsi_cap.get(p.level, 90.0))

    # ── 코인 진입 체결 게이트 + 공격적 체결 + 손절하한(매수↔매도 코히어런스) ──
    # 매수를 완화(호가벽/스프레드)하고 호가 맨앞에서 즉시 체결하되, 스프레드만으로
    # 손절이 터지지 않게 손절 하한을 함께 넓힌다. 낮은 단계는 보수적(수동 체결).
    _lvl = p.level
    # 매수벽 비율(bid≥ask×r): 낮을수록 완화. 1.0(기본) → 0.3(9~10)
    _wall = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0.7, 7: 0.6, 8: 0.5, 9: 0.35, 10: 0.3}
    # 스프레드 허용: 0.25% → 0.6%(9~10)
    _spr = {1: 0.25, 2: 0.25, 3: 0.25, 4: 0.30, 5: 0.30, 6: 0.35, 7: 0.40, 8: 0.45, 9: 0.55, 10: 0.6}
    e["CRYPTO_MIN_BID_ASK_RATIO"] = str(_wall.get(_lvl, 1.0))
    e["CRYPTO_MAX_SPREAD_PCT"] = str(_spr.get(_lvl, 0.25))
    # 공격적 체결(호가 맨앞=best ask 즉시 체결)은 9~10단계만
    e["CRYPTO_AGGRESSIVE_FILL"] = "true" if _lvl >= 9 else "false"
    # 손절 하한(가장 타이트해도 이 값): 공격 체결 단계는 스프레드+수수료보다 넓게.
    # sl_pct_max(=손절 상한, 음수 중 0에 가까운 쪽)를 더 음수로 밀어 타이트 손절 방지.
    _sl_floor = {9: -1.3, 10: -1.5}
    if _lvl in _sl_floor:
        e["CRYPTO_SL_PCT_MAX"] = str(_sl_floor[_lvl])
    else:
        e.pop("CRYPTO_SL_PCT_MAX", None)

    # ── 주식 익절/손절도 단계 연동 (높을수록 익절 목표↑=수익 달리기, 손절폭↑=여유) ──
    # 분수 단위(0.15 = +15%, -0.07 = -7%)
    lvl = p.level
    e["DEEPSIGNAL_STOCK_TP_PCT"] = str(round(0.08 + (lvl - 1) * 0.035, 3))    # L1 +8% → L10 +40%
    e["DEEPSIGNAL_STOCK_SL_PCT"] = str(round(-(0.04 + (lvl - 1) * 0.016), 3))  # L1 -4% → L10 -18%
    _log_aggression_change(p)
    return p


def _log_aggression_change(p: "AggressionProfile") -> None:
    """공격성 단계가 바뀐 시점을 append-only 로그에 남긴다.

    거래 시각을 나중에 당시 단계로 매핑하기 위함. 변경된 경우에만 1줄 추가
    (상태파일 outputs/AGGRESSION_CURRENT.json 과 비교해 프로세스 간 중복 방지).
    """
    try:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from pathlib import Path as _P

        out = _P(__file__).resolve().parents[2] / "outputs"
        out.mkdir(parents=True, exist_ok=True)
        state = out / "AGGRESSION_CURRENT.json"
        prev = None
        if state.exists():
            try:
                prev = _json.loads(state.read_text(encoding="utf-8")).get("level")
            except Exception:
                prev = None
        if prev == p.level:
            return  # 변경 없음
        kst = _tz(_td(hours=9))
        ts = _dt.now(kst).isoformat(timespec="seconds")
        rec = {
            "ts": ts, "level": p.level, "band": p.band,
            "position_mult": p.position_mult, "leverage_max": p.leverage_max,
            "entry_threshold_mult": p.entry_threshold_mult,
            "take_profit_mode": p.take_profit_mode,
            "edge_gate_enforced": p.edge_gate_enforced,
            "max_change_rate": os.environ.get("CRYPTO_MAX_CHANGE_RATE"),
            "max_rsi": os.environ.get("CRYPTO_MAX_RSI"),
            "ml_buy_threshold": os.environ.get("CRYPTO_ML_BUY_THRESHOLD"),
            "prev_level": prev,
        }
        with open(out / "aggression_history.jsonl", "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        state.write_text(_json.dumps({"level": p.level, "band": p.band, "ts": ts},
                                     ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def refresh_and_apply(env_path: str | None = None) -> "AggressionProfile":
    """.env에서 최신 공격성 단계만 다시 읽어 적용 (러너 tick용 — 재시작 없이 반영)."""
    try:
        from pathlib import Path as _P
        path = env_path or str(_P(__file__).resolve().parents[2] / ".env")
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("DEEPSIGNAL_AGGRESSION="):
                    os.environ["DEEPSIGNAL_AGGRESSION"] = str(clamp_level(s.split("=", 1)[1].strip()))
                    break
    except Exception:
        pass
    return apply_aggression()


def position_mult() -> float:
    try:
        return max(0.1, float(os.environ.get("DEEPSIGNAL_POSITION_MULT", "1.0")))
    except ValueError:
        return 1.0


def entry_threshold_mult() -> float:
    try:
        return max(0.3, float(os.environ.get("DEEPSIGNAL_ENTRY_THRESHOLD_MULT", "1.0")))
    except ValueError:
        return 1.0


def tp_mode() -> str:
    return os.environ.get("DEEPSIGNAL_TP_MODE", "dynamic").strip() or "dynamic"
