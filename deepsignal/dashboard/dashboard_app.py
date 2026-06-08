"""tkinter 조회 전용 대시보드 (로컬, 실주문 없음)."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Any

from deepsignal.dashboard.dashboard_data import load_dashboard_data


def _fmt_num(v: Any, nd: int = 2) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(x):
        return "-"
    return f"{x:.{nd}f}"


def _fmt_cell(v: Any) -> str:
    if v is None:
        return "-"
    s = str(v).replace("\n", " ").replace("\r", " ")
    if len(s) > 200:
        return s[:197] + "..."
    return s


def _clear_tree(tree: ttk.Treeview) -> None:
    for iid in tree.get_children():
        tree.delete(iid)


class DashboardApp:
    """signals / backtests / paper 탭 조회."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.root = tk.Tk()
        self.root.title("DeepSignal Dashboard (read-only)")
        self.root.minsize(1020, 620)
        self.root.geometry("1100x680")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="DB_PATH:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(top, text=db_path, wraplength=700).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side=tk.RIGHT, padx=(8, 0))

        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self._tab_sig = ttk.Frame(nb, padding=4)
        self._tab_bt = ttk.Frame(nb, padding=4)
        self._tab_paper = ttk.Frame(nb, padding=4)
        nb.add(self._tab_sig, text="Signals")
        nb.add(self._tab_bt, text="Backtests")
        nb.add(self._tab_paper, text="Paper")

        self._sig_status = ttk.Label(self._tab_sig, text="")
        self._sig_status.pack(anchor=tk.W, pady=(0, 4))
        sig_cols = ("symbol", "signal_date", "action", "final_score", "confidence", "reason")
        self._sig_tree = ttk.Treeview(
            self._tab_sig, columns=sig_cols, show="headings", height=18, selectmode=tk.BROWSE
        )
        for c, w in zip(
            sig_cols,
            (90, 110, 130, 100, 90, 420),
        ):
            self._sig_tree.heading(c, text=c)
            self._sig_tree.column(c, width=w, stretch=(c == "reason"))
        sy = ttk.Scrollbar(self._tab_sig, orient=tk.VERTICAL, command=self._sig_tree.yview)
        self._sig_tree.configure(yscrollcommand=sy.set)
        self._sig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

        self._bt_status = ttk.Label(self._tab_bt, text="")
        self._bt_status.pack(anchor=tk.W, pady=(0, 4))
        bt_cols = (
            "symbol",
            "start_date",
            "end_date",
            "final_value",
            "total_return_pct",
            "trade_count",
            "win_rate",
            "max_drawdown_pct",
        )
        self._bt_tree = ttk.Treeview(
            self._tab_bt, columns=bt_cols, show="headings", height=18, selectmode=tk.BROWSE
        )
        bt_widths = (80, 100, 100, 100, 120, 90, 90, 110)
        for c, w in zip(bt_cols, bt_widths):
            self._bt_tree.heading(c, text=c)
            self._bt_tree.column(c, width=w, stretch=False)
        by = ttk.Scrollbar(self._tab_bt, orient=tk.VERTICAL, command=self._bt_tree.yview)
        self._bt_tree.configure(yscrollcommand=by.set)
        self._bt_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        by.pack(side=tk.RIGHT, fill=tk.Y)

        self._paper_snap_frame = ttk.LabelFrame(self._tab_paper, text="Latest snapshot", padding=6)
        self._paper_snap_frame.pack(fill=tk.X, pady=(0, 8))
        self._paper_snap_labels: dict[str, ttk.Label] = {}
        for key, label in [
            ("cash", "cash"),
            ("equity", "equity"),
            ("positions_value", "positions_value"),
            ("last_action", "last_action"),
            ("reason", "reason"),
        ]:
            row = ttk.Frame(self._paper_snap_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{label}:", width=18).pack(side=tk.LEFT)
            self._paper_snap_labels[key] = ttk.Label(row, text="-", wraplength=780)
            self._paper_snap_labels[key].pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._pos_status = ttk.Label(self._tab_paper, text="")
        self._pos_status.pack(anchor=tk.W)
        pf = ttk.LabelFrame(self._tab_paper, text="paper_positions", padding=4)
        pf.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
        pos_cols = ("symbol", "quantity", "avg_price", "updated_at")
        self._pos_tree = ttk.Treeview(pf, columns=pos_cols, show="headings", height=6, selectmode=tk.BROWSE)
        for c, w in zip(pos_cols, (100, 90, 100, 160)):
            self._pos_tree.heading(c, text=c)
            self._pos_tree.column(c, width=w)
        py = ttk.Scrollbar(pf, orient=tk.VERTICAL, command=self._pos_tree.yview)
        self._pos_tree.configure(yscrollcommand=py.set)
        self._pos_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        py.pack(side=tk.RIGHT, fill=tk.Y)

        self._tr_status = ttk.Label(self._tab_paper, text="")
        self._tr_status.pack(anchor=tk.W)
        tf = ttk.LabelFrame(self._tab_paper, text="paper_trades (latest 20)", padding=4)
        tf.pack(fill=tk.BOTH, expand=True)
        tr_cols = ("symbol", "trade_date", "side", "price", "quantity", "cash_before", "cash_after", "reason")
        self._tr_tree = ttk.Treeview(tf, columns=tr_cols, show="headings", height=8, selectmode=tk.BROWSE)
        tr_w = (80, 100, 60, 80, 70, 100, 100, 280)
        for c, w in zip(tr_cols, tr_w):
            self._tr_tree.heading(c, text=c)
            self._tr_tree.column(c, width=w, stretch=(c == "reason"))
        ty = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self._tr_tree.yview)
        self._tr_tree.configure(yscrollcommand=ty.set)
        self._tr_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ty.pack(side=tk.RIGHT, fill=tk.Y)

        self.refresh()

    def refresh(self) -> None:
        data = load_dashboard_data(self._db_path)

        _clear_tree(self._sig_tree)
        if not data.signals:
            self._sig_status.config(text="저장된 시그널이 없습니다.")
        else:
            self._sig_status.config(text=f"총 {len(data.signals)}건 (최신 20)")
            for r in data.signals:
                self._sig_tree.insert(
                    "",
                    tk.END,
                    values=(
                        _fmt_cell(r.get("symbol")),
                        _fmt_cell(r.get("signal_date")),
                        _fmt_cell(r.get("action")),
                        _fmt_num(r.get("technical_score")),
                        _fmt_num(r.get("news_score")),
                        _fmt_num(r.get("final_score")),
                        _fmt_num(r.get("confidence")),
                        _fmt_cell(r.get("reason")),
                    ),
                )

        _clear_tree(self._bt_tree)
        if not data.backtests:
            self._bt_status.config(text="저장된 백테스트 결과가 없습니다.")
        else:
            self._bt_status.config(text=f"총 {len(data.backtests)}건 (최신 20)")
            for r in data.backtests:
                wr = r.get("win_rate")
                wrs = _fmt_num(wr) + "%" if wr is not None else "-"
                mdd = r.get("max_drawdown_pct")
                mdds = _fmt_num(mdd) + "%" if mdd is not None else "-"
                self._bt_tree.insert(
                    "",
                    tk.END,
                    values=(
                        _fmt_cell(r.get("symbol")),
                        _fmt_cell(r.get("start_date")),
                        _fmt_cell(r.get("end_date")),
                        _fmt_num(r.get("final_value")),
                        _fmt_num(r.get("total_return_pct")) + "%",
                        _fmt_cell(r.get("trade_count")),
                        wrs,
                        mdds,
                    ),
                )

        snap = data.paper_snapshot
        if snap is None:
            for k in self._paper_snap_labels:
                self._paper_snap_labels[k].config(text="(없음)")
        else:
            self._paper_snap_labels["cash"].config(text=_fmt_num(snap.get("cash")))
            self._paper_snap_labels["equity"].config(text=_fmt_num(snap.get("equity")))
            self._paper_snap_labels["positions_value"].config(text=_fmt_num(snap.get("positions_value")))
            self._paper_snap_labels["last_action"].config(text=_fmt_cell(snap.get("last_action")))
            self._paper_snap_labels["reason"].config(text=_fmt_cell(snap.get("reason")))

        _clear_tree(self._pos_tree)
        if not data.paper_positions:
            self._pos_status.config(text="paper_positions: 저장된 포지션이 없습니다.")
        else:
            self._pos_status.config(text=f"paper_positions: {len(data.paper_positions)}건")
            for p in data.paper_positions:
                self._pos_tree.insert(
                    "",
                    tk.END,
                    values=(
                        _fmt_cell(p.get("symbol")),
                        _fmt_cell(p.get("quantity")),
                        _fmt_num(p.get("avg_price")),
                        _fmt_cell(p.get("updated_at")),
                    ),
                )

        _clear_tree(self._tr_tree)
        if not data.paper_trades:
            self._tr_status.config(text="paper_trades: 저장된 체결이 없습니다.")
        else:
            self._tr_status.config(text=f"paper_trades: {len(data.paper_trades)}건 (최신 20)")
            for t in data.paper_trades:
                self._tr_tree.insert(
                    "",
                    tk.END,
                    values=(
                        _fmt_cell(t.get("symbol")),
                        _fmt_cell(t.get("trade_date")),
                        _fmt_cell(t.get("side")),
                        _fmt_num(t.get("price")),
                        _fmt_cell(t.get("quantity")),
                        _fmt_num(t.get("cash_before")),
                        _fmt_num(t.get("cash_after")),
                        _fmt_cell(t.get("reason")),
                    ),
                )

    def run(self) -> None:
        self.root.mainloop()


def run_dashboard(db_path: str) -> None:
    """DB 경로로 대시보드를 연다."""
    DashboardApp(db_path).run()
