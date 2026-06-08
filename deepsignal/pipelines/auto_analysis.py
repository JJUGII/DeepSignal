"""run-daily 확장: 밸류·집중도·실계좌 peak 자동 분석."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepsignal.analysis.portfolio_concentration import load_real_concentration_from_db
from deepsignal.analyzer.valuation.valuation_analyzer import ValuationAnalyzer
from deepsignal.pipelines.daily_pipeline import PipelineStepResult


def run_valuation_for_symbols(
    path_str: str,
    symbols: tuple[str, ...],
) -> dict[str, Any]:
    """종목별 yfinance 밸류에이션 (네트워크)."""
    analyzer = ValuationAnalyzer()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for sym in symbols:
        u = sym.strip().upper()
        if not u:
            continue
        try:
            res = analyzer.analyze_symbol(u)
            rows.append(res.to_dict())
            print(f"Valuation {u}: score={res.valuation_score} mispricing={res.mispricing_pct}")
        except Exception as exc:
            errors.append(f"{u}: {type(exc).__name__}: {exc}")
    return {"symbols": list(symbols), "valuations": rows, "errors": errors}


def run_concentration_check(path_str: str, *, broker: str = "kis") -> dict[str, Any]:
    """실계좌 IPS 5% 집중도."""
    result = load_real_concentration_from_db(path_str, broker=broker)
    print(f"Concentration check: {result.status} (cap {result.cap_fraction:.2%})")
    for w in result.warnings:
        print(f"  WARNING: {w}")
    for a in result.alerts:
        print(f"  ALERT: {a}")
    return result.to_dict()


def write_auto_analysis_summary(
    path_str: str,
    *,
    symbols: tuple[str, ...],
    valuation_payload: dict[str, Any],
    concentration_payload: dict[str, Any],
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """AUTO_ANALYSIS_SUMMARY JSON/MD."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    body = {
        "generated_at": now.isoformat(timespec="seconds"),
        "db_path": path_str,
        "symbols": list(symbols),
        "valuation": valuation_payload,
        "concentration": concentration_payload,
        "note": "peak_price는 live-sync-account 시 자동 갱신됩니다.",
    }
    json_path = root / f"auto_analysis_summary_{stamp}.json"
    md_path = root / "AUTO_ANALYSIS_SUMMARY.md"
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DeepSignal — 자동 통합 분석 요약",
        "",
        f"- 생성: {body['generated_at']}",
        f"- DB: `{path_str}`",
        "",
        "## 밸류에이션 (yfinance)",
        "",
    ]
    for row in valuation_payload.get("valuations") or []:
        sym = row.get("symbol")
        vs = row.get("valuation_score")
        mp = row.get("mispricing_pct")
        lines.append(f"- **{sym}**: score={vs}, mispricing={mp}")
    if valuation_payload.get("errors"):
        lines.append("")
        lines.append("### 밸류 오류")
        for e in valuation_payload["errors"]:
            lines.append(f"- {e}")

    lines.extend(["", "## IPS 집중도 (실계좌)", ""])
    conc = concentration_payload
    lines.append(f"- 상태: **{conc.get('status')}** (상한 {conc.get('cap_fraction')})")
    for item in conc.get("items") or []:
        lines.append(f"- {item.get('symbol')}: {item.get('weight', 0):.2%} — {item.get('message')}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return json_path, md_path


def run_full_analysis_extras(
    path_str: str,
    symbols: tuple[str, ...],
    *,
    broker: str = "kis",
    skip_valuation: bool = False,
) -> tuple[PipelineStepResult, PipelineStepResult]:
    """run-daily 마지막에 호출."""
    val_raw: dict[str, Any] = {"skipped": True}
    conc_raw: dict[str, Any] = {}
    try:
        if skip_valuation:
            val_st = "skipped"
        else:
            val_raw = run_valuation_for_symbols(path_str, symbols)
            val_st = "success" if not val_raw.get("errors") else "partial_failed"
        conc_raw = run_concentration_check(path_str, broker=broker)
        write_auto_analysis_summary(
            path_str,
            symbols=symbols,
            valuation_payload=val_raw,
            concentration_payload=conc_raw,
        )
        conc_st = str(conc_raw.get("status") or "OK").upper()
        if conc_st in ("ALERT", "WARNING"):
            conc_st = "partial_failed" if conc_st == "WARNING" else "failed"
        else:
            conc_st = "success" if conc_st != "NO_DATA" else "skipped"
        return (
            PipelineStepResult("valuation-batch", val_st, "", val_raw),
            PipelineStepResult("concentration-check", conc_st, conc_raw.get("status", ""), conc_raw),
        )
    except Exception as exc:
        return (
            PipelineStepResult("valuation-batch", "failed", str(exc), val_raw),
            PipelineStepResult("concentration-check", "failed", str(exc), conc_raw),
        )
