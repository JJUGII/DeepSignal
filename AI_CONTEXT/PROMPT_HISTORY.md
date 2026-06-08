# Deepsignal — Prompt History

Append-only log. Add newest entries at the top or bottom consistently.

| Date | Work Goal | Cursor Prompt Summary | Result Status |
|------|-----------|-----------------------|---------------|
| TODO | Initial context bootstrap | Created standard AI_CONTEXT structure | Draft |
| 2026-05-17 | AI_CONTEXT standardization | Add reusable `init-context` command for project context/status/TODO/rules/history bootstrap without overwrite | Implemented |
| 2026-05-17 | Safety Audit Dashboard Link | Link `SAFETY_AUDIT.md` and latest `safety_audit_*.json` from report index, HTML dashboard, and open-dashboard | Implemented |
| 2026-05-17 | Dashboard Archive Viewer | Build read-only local archive viewer for historical operations reports and link it from report-index/open-dashboard | Implemented |
| 2026-05-17 | Archive Viewer Filter / UX | Add filters, sorting, Needs Attention, latest_by_type and richer JSON export to Archive Viewer | Implemented |
| 2026-05-17 | Archive Viewer Korean Operator UI | Add Korean labels for operator-facing archive/report UI while preserving raw JSON/status values | Implemented |
| 2026-05-17 | Archive Viewer Print / Export Mode | Add print CSS, CSV export, Markdown summary export, report-index/open-dashboard links, and opt-out CLI flags | Implemented |
| 2026-05-17 | Archive Viewer Saved Filter Presets | Add static preset JSON and inline HTML preset controls for common operator filters | Implemented |
| 2026-05-17 | Archive Trend Analytics | Add metadata-based trend analytics, HTML/Markdown 운영 추세, JSON export fields, and `--trend-days` option | Implemented |
| 2026-05-17 | AI Live Trade Recommendation Engine | Add `ai-live-recommend` for AI BUY/SELL/HOLD/REDUCE/INCREASE recommendations and manual-approval order plan output | Implemented |
| 2026-05-18 | AI Recommendation Backtest / Paper Validation | Add `validate-ai-recommendation` in-memory validation with metrics, JSON/Markdown/CSV outputs, and no live/paper writes | Implemented |
| 2026-05-18 | AI Recommendation Advanced Validation Metrics | Add Sharpe/profit factor/drawdown detail/benchmark comparison/equity curve and CSV metric extensions | Implemented |
| 2026-05-19 | AI Recommendation Cost / Slippage Validation | Add deterministic cost model for commission/tax/slippage/min-max order value and extend validation reports/metrics/CLI | Implemented |
| 2026-05-19 | AI Recommendation Portfolio Risk Validation | Add sector concentration, symbol weight, high-correlation pair, concentration/diversification score validation outputs | Implemented |
| 2026-05-19 | AI Recommendation Liquidity Constraint Validation | Add market_prices volume-based liquidity limits, quantity adjustment/skip metadata, and validation report extensions | Implemented |
| 2026-05-19 | AI Recommendation FX / Currency-Aware Validation | Add local FX rates, symbol currency mapping, fallback conversion, and multi-currency validation metadata | Implemented |
| 2026-05-19 | Telegram Approval Trading Workflow | Add Telegram button approval workflow with token/hash/chat_id/expiry/limit checks, approval audit, and terminal execution guidance only | Implemented |
| 2026-05-19 | Simplified Approved Execution Command | Add `execute-last-approved` and `execute-approved --request-id` to run approved plans from terminal after Telegram audit validation | Implemented |
| 2026-05-19 | Daily AI Trading Workflow | Add daily AI plan/report/status commands, latest AI plan pointer, and Telegram approval handoff flow | Implemented |
| 2026-05-19 | Daily AI Workflow Dashboard Integration | Show AI_DAILY_* workflow status in report-index, open-dashboard, archive-viewer, and safety-audit without execution calls | Implemented |
