"""DM3 — K-GSQS/코인 점수 캘리브레이션 (score→P(win)) + 수수료 허들 판정.

목적: 점수가 실제 승률·기대수익과 어떤 관계인지 outcome 라벨로 적합하고, **어떤 점수
임계값이 수수료를 넘는 기대수익을 주는가**를 판정한다. IC 분해(2026-06-05)에서 국내
K-GSQS 총점이 반(反)예측적이고 코인은 평탄임이 드러났으므로, 손튜닝을 라이브에 자동
적용하지 않는다 — 이 도구는 **진단·임계값 게이팅**용이다.

방법:
- 단조회귀 PAVA(Pool Adjacent Violators)로 score→P(win) 단조증가 적합(외부 의존 없음).
- 점수 버킷별 승률·평균 net수익(= ret − 왕복수수료) 표.
- 판정: 최상위 버킷 net수익>0 이고 단조성이 양(+)이면 '거래가능 임계 존재', 아니면 'OFF 권고'.

출력: outputs/KGSQS_CALIBRATION.json + 콘솔표.
사용: PYTHONPATH=. ./.venv/bin/python scripts/calibrate_kgsqs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROUND_TRIP_FEE = {"국내주식": 0.0063, "코인": 0.0020}  # 왕복 수수료+슬리피지 가정
HORIZON = "ret_5m"


def load(path: str) -> list[dict]:
    rows = []
    p = Path(path)
    if not p.is_file():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("outcome_complete") and d.get("score") is not None and HORIZON in d:
            rows.append(d)
    return rows


def pava(x: list[float], y: list[float]) -> list[float]:
    """단조증가 PAVA. x오름차순 정렬 가정, y의 단조회귀 적합값 반환."""
    n = len(y)
    if n == 0:
        return []
    # (값, 가중치) 블록 풀링
    vals = list(y)
    wts = [1.0] * n
    # 인덱스 스택
    blocks: list[list[float]] = []  # [sum, weight, level]
    for i in range(n):
        cur = [vals[i], wts[i], vals[i]]
        blocks.append(cur)
        while len(blocks) >= 2 and blocks[-2][2] > blocks[-1][2]:
            s2, w2, _ = blocks.pop()
            s1, w1, _ = blocks.pop()
            s, w = s1 + s2, w1 + w2
            blocks.append([s, w, s / w])
    out = []
    for s, w, lvl in blocks:
        out.extend([lvl] * int(w))
    return out


def analyze(path: str, label: str) -> dict:
    rows = load(path)
    n = len(rows)
    fee = ROUND_TRIP_FEE.get(label, 0.002)
    if n < 20:
        print(f"\n### {label}: 샘플 부족({n})")
        return {"label": label, "n": n, "tradable": False, "reason": "샘플 부족"}

    rows.sort(key=lambda r: float(r["score"]))
    scores = [float(r["score"]) for r in rows]
    wins = [1.0 if float(r[HORIZON]) > 0 else 0.0 for r in rows]
    fitted = pava(scores, wins)  # score 오름차순에 대한 단조 P(win)

    # 버킷(5분위) 요약
    q = max(1, n // 5)
    print(f"\n### {label}  (N={n}, 왕복수수료 {fee*100:.2f}%, 호라이즌 {HORIZON})")
    print(f"{'점수구간':>14} {'n':>5} {'승률':>6} {'평균ret':>9} {'net(수수료후)':>12} {'단조P(win)':>10}")
    buckets = []
    for b in range(5):
        lo = b * q
        hi = n if b == 4 else (b + 1) * q
        seg = rows[lo:hi]
        if not seg:
            continue
        srng = (float(seg[0]["score"]), float(seg[-1]["score"]))
        wr = sum(1 for r in seg if float(r[HORIZON]) > 0) / len(seg)
        mret = sum(float(r[HORIZON]) for r in seg) / len(seg)
        net = mret - fee
        pmono = sum(fitted[lo:hi]) / len(seg)
        buckets.append({"range": srng, "n": len(seg), "winrate": round(wr, 3),
                        "mean_ret": round(mret, 5), "net": round(net, 5), "p_win_mono": round(pmono, 3)})
        print(f"  {srng[0]:5.0f}~{srng[1]:5.0f} {len(seg):5d} {wr:6.2f} {mret*100:8.3f}% "
              f"{net*100:11.3f}% {pmono:10.3f}")

    top = buckets[-1] if buckets else {}
    mono_increasing = all(
        buckets[i]["p_win_mono"] <= buckets[i + 1]["p_win_mono"] + 1e-9
        for i in range(len(buckets) - 1)
    )
    # 거래가능 임계: 최상위 버킷 net>0 AND 단조 양(+) 추세(상·하위 P(win) 차)
    spread = (buckets[-1]["p_win_mono"] - buckets[0]["p_win_mono"]) if len(buckets) >= 2 else 0.0
    tradable = bool(top.get("net", -1) > 0 and spread > 0.02)
    verdict = ("✅ 거래가능 임계 존재" if tradable
               else "❌ 어떤 임계도 수수료 못 넘음 → 자동매매 OFF 권고(게이트 유지)")
    print(f"  판정: {verdict}  (최상위 net {top.get('net',0)*100:+.3f}%, P(win) 상-하 차 {spread:+.3f})")
    return {"label": label, "n": n, "fee": fee, "buckets": buckets,
            "monotone_increasing": mono_increasing, "pwin_spread": round(spread, 4),
            "top_net": top.get("net"), "tradable": tradable, "verdict": verdict}


def main():
    out = {
        "horizon": HORIZON,
        "results": [
            analyze("output/kis_stream/kstock/signal_log.jsonl", "국내주식"),
            analyze("outputs/signal_log.jsonl", "코인"),
        ],
    }
    dest = Path("outputs/KGSQS_CALIBRATION.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n저장: {dest}")
    any_tradable = any(r.get("tradable") for r in out["results"])
    print("\n종합:", "일부 자산 거래가능 임계 존재" if any_tradable
          else "전 자산 수수료 허들 미달 — 단타 자동매매는 게이트로 닫아둠이 정답(IC·백테스트와 일치)")


if __name__ == "__main__":
    main()
