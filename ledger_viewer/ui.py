"""Tk desktop interface; Jev networking is opt-in for column suggestions."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .checks import run_checks
from .grid_lines import DashedGrid
from .importer import REPORT_KINDS, ImportSpec, classify_sheet, discover_sheets, file_hash, format_money, guess_header, guess_mapping, header_for_row, rows, sheet_names, text_value
from .jev_mapping import delete_api_key, get_api_key, preview_sheet, save_api_key, suggest_mapping
from .reconciliation import empty_plan, load_plan, save_plan
from .related_parties import related_party_match
from .store import LedgerStore
from .voucher_notes import export_marks_md, list_marks, mark_for_entry, remove_mark, save_mark, save_mark_by_key, source_account, voucher_key, voucher_lines_for_key


FIELD_LABELS = {
    "date": "日期", "fiscal_year": "会计年度", "period": "期间", "voucher_no": "凭证号", "voucher_type": "凭证字", "account_code": "科目编码",
    "account_name": "科目名称", "summary": "摘要", "debit": "借方金额", "credit": "贷方金额",
    "counterparty": "往来单位", "auxiliary": "辅助核算", "opening_debit": "期初借方",
    "opening_credit": "期初贷方", "period_debit": "本期借方", "period_credit": "本期贷方",
    "year_debit": "本年累计借方", "year_credit": "本年累计贷方",
    "closing_debit": "期末借方", "closing_credit": "期末贷方",
    "opening_balance": "期初余额", "closing_balance": "期末余额", "direction": "余额方向",
    "currency": "币别", "original_amount": "原币金额", "maker": "制单人",
    "left_item": "左侧项目", "left_amount_1": "左侧金额 1", "left_amount_2": "左侧金额 2", "left_amount_3": "左侧金额 3",
    "right_item": "右侧项目", "right_amount_1": "右侧金额 1", "right_amount_2": "右侧金额 2", "right_amount_3": "右侧金额 3",
}

REPORT_LABELS = {"balance_sheet": "资产负债表", "income_statement": "利润表"}

VIEW_COLUMNS = {
    "balance": [("code", "科目编码", 125), ("name", "科目名称", 200), ("od", "期初借方", 115), ("oc", "期初贷方", 115), ("pd", "本期借方", 115), ("pc", "本期贷方", 115), ("yd", "本年累计借方", 125), ("yc", "本年累计贷方", 125), ("cd", "期末借方", 115), ("cc", "期末贷方", 115), ("n", "明细行数", 80)],
    "detail": [("marked", "关注", 55), ("date", "日期", 105), ("year", "年度", 70), ("period", "期间", 65), ("voucher", "凭证", 120), ("code", "科目编码", 110), ("name", "科目名称", 180), ("summary", "摘要", 250), ("counterparty", "往来单位", 145), ("related", "用户标注关联方", 145), ("aux", "核算维度", 145), ("currency", "币别", 80), ("original", "原币金额", 100), ("debit", "借方", 105), ("credit", "贷方", 105), ("maker", "制单人", 90), ("source", "来源行", 200)],
    "voucher": [("code", "科目编码", 90), ("name", "科目名称", 125), ("summary", "摘要", 190), ("aux", "核算维度", 140), ("currency", "币别", 75), ("original", "原币金额", 100), ("debit", "借方", 90), ("credit", "贷方", 90), ("source", "来源行", 135)],
}

DEFAULT_COLUMNS = {
    "balance": [key for key, _, _ in VIEW_COLUMNS["balance"]],
    "detail": ["marked", "date", "period", "voucher", "code", "name", "summary", "counterparty", "related", "debit", "credit", "source"],
    "voucher": ["code", "name", "summary", "debit", "credit", "source"],
}

MONEY_COLUMN_KEYS = {"od", "oc", "pd", "pc", "yd", "yc", "cd", "cc", "debit", "credit", "original", "left_a", "left_b", "left_c", "right_a", "right_b", "right_c", "a", "b"}


def visible_columns(kind: str, saved: list[str] | None) -> list[str]:
    """Keep known columns in table order; use the default for stale preferences."""
    available = [key for key, _, _ in VIEW_COLUMNS[kind]]
    if saved is None:
        return DEFAULT_COLUMNS[kind].copy()
    selected = set(saved)
    result = [key for key in available if key in selected]
    return result or DEFAULT_COLUMNS[kind].copy()


def amount(cents: int) -> str:
    return format_money(cents, comma=True) if cents else ""


def report_amount(cents: int | None) -> str:
    return format_money(cents, comma=True) if cents is not None else ""


def tree(parent: tk.Widget, columns: list[tuple[str, str, int]]) -> ttk.Treeview:
    holder = ttk.Frame(parent)
    holder.pack(fill="both", expand=True)
    view = ttk.Treeview(holder, columns=[c[0] for c in columns], show="headings", selectmode="browse")
    ybar = ttk.Scrollbar(holder, orient="vertical", command=view.yview)
    xbar = ttk.Scrollbar(holder, orient="horizontal", command=view.xview)
    view.dashed_grid = DashedGrid(view)
    def on_y_scroll(first: str, last: str) -> None:
        ybar.set(first, last)
        view.dashed_grid.schedule()
    def on_x_scroll(first: str, last: str) -> None:
        xbar.set(first, last)
        view.dashed_grid.schedule()
    view.configure(yscrollcommand=on_y_scroll, xscrollcommand=on_x_scroll)
    view.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    for key, label, width in columns:
        view.heading(key, text=label, anchor="e" if key in MONEY_COLUMN_KEYS else "w")
        view.column(key, width=width, minwidth=65, stretch=False, anchor="e" if key in MONEY_COLUMN_KEYS else "w")
    view.tag_configure("debit_row", foreground="#355E70")
    view.tag_configure("credit_row", foreground="#776B58")
    view.tag_configure("difference", background="#FFF9EF")
    view.tag_configure("pending", background="#F4F8FA")
    return view


class ColumnDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, kind: str, selected: list[str], on_change):
        super().__init__(parent)
        self.title("设置显示列")
        self.transient(parent)
        self.resizable(False, False)
        self.on_change = on_change
        self.vars = {key: tk.BooleanVar(value=key in selected) for key, _, _ in VIEW_COLUMNS[kind]}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="勾选要显示的列，修改后立即生效。", foreground="#555").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for index, (key, label, _) in enumerate(VIEW_COLUMNS[kind]):
            ttk.Checkbutton(body, text=label, variable=self.vars[key], command=self._apply).grid(row=1 + index // 2, column=index % 2, sticky="w", padx=(0, 26), pady=4)
        row = 1 + (len(VIEW_COLUMNS[kind]) + 1) // 2
        ttk.Button(body, text="全部显示", command=self._show_all).grid(row=row, column=0, sticky="w", pady=(14, 0))
        ttk.Button(body, text="恢复默认", command=lambda: self._select(DEFAULT_COLUMNS[kind])).grid(row=row, column=1, sticky="w", pady=(14, 0))
        ttk.Button(body, text="完成", command=self.destroy).grid(row=row + 1, column=1, sticky="e", pady=(14, 0))
        self.grab_set()

    def _select(self, keys: list[str]) -> None:
        for key, var in self.vars.items():
            var.set(key in keys)
        self._apply()

    def _show_all(self) -> None:
        self._select(list(self.vars))

    def _apply(self) -> None:
        selected = [key for key, var in self.vars.items() if var.get()]
        if not selected:
            messagebox.showinfo("至少保留一列", "请至少保留一列用于查看。", parent=self)
            self.vars[next(iter(self.vars))].set(True)
            selected = [next(iter(self.vars))]
        self.on_change(selected)


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.title("偏好设置")
        self.resizable(False, False)
        self.transient(parent)
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="凭证打开方式").pack(anchor="w")
        current = parent.store.setting("voucher_open_mode", "replace") if parent.store else "replace"
        self.mode = tk.StringVar(value=current if current in ("replace", "multiple") else "replace")
        for label, value in (("新凭证自动关闭前一张", "replace"), ("连续打开，保留已打开凭证", "multiple")):
            ttk.Radiobutton(body, text=label, variable=self.mode, value=value, command=self._save_mode, state="normal" if parent.store else "disabled").pack(anchor="w", pady=3)
        ttk.Separator(body).pack(fill="x", pady=12)
        font_row = ttk.Frame(body)
        font_row.pack(fill="x")
        ttk.Label(font_row, text="字体大小").pack(side="left")
        self.font_size = tk.StringVar(value=str(parent.controller.font_size))
        font_combo = ttk.Combobox(font_row, textvariable=self.font_size, values=tuple(str(size) for size in range(9, 21)), state="readonly", width=5)
        font_combo.pack(side="left", padx=8)
        font_combo.bind("<<ComboboxSelected>>", lambda _event: parent.controller.set_font_size(int(self.font_size.get())))
        ttk.Separator(body).pack(fill="x", pady=12)
        ttk.Label(body, text="Jev 表格识别（可选）").pack(anchor="w")
        key_row = ttk.Frame(body)
        key_row.pack(fill="x", pady=(5, 0))
        self.api_key = tk.StringVar()
        ttk.Entry(key_row, textvariable=self.api_key, show="•", width=29).pack(side="left", fill="x", expand=True)
        ttk.Button(key_row, text="保存密钥", command=self._save_api_key).pack(side="left", padx=5)
        ttk.Button(key_row, text="删除密钥", command=self._delete_api_key).pack(side="left")
        self.key_status = tk.StringVar()
        ttk.Label(body, textvariable=self.key_status, foreground="#555").pack(anchor="w", pady=(4, 0))
        self._refresh_key_status()
        ttk.Label(body, text="密钥保存在系统钥匙串；只有点击“Jev 建议列”并确认发送范围时才联网。", foreground="#555").pack(anchor="w", pady=(7, 0))
        ttk.Label(body, text="字体适用于所有窗口；凭证打开方式只适用于当前项目。", foreground="#555").pack(anchor="w", pady=(14, 0))
        ttk.Button(body, text="完成", command=self.destroy).pack(anchor="e", pady=(12, 0))

    def _save_mode(self) -> None:
        if self.parent.store:
            self.parent.store.save_setting("voucher_open_mode", self.mode.get())

    def _refresh_key_status(self) -> None:
        try:
            self.key_status.set("已保存密钥" if get_api_key() else "尚未保存密钥")
        except Exception as exc:
            self.key_status.set(f"无法访问系统钥匙串：{exc}")

    def _save_api_key(self) -> None:
        try:
            save_api_key(self.api_key.get())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.api_key.set("")
        self._refresh_key_status()

    def _delete_api_key(self) -> None:
        try:
            delete_api_key()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.api_key.set("")
        self._refresh_key_status()


class RelatedPartiesDialog(tk.Toplevel):
    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.title("当前项目 · 关联方名单")
        self.geometry("650x530")
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="名单由用户指定；只对明细中的往来单位、辅助核算名称作精确匹配，不构成法律关系认定。", foreground="#555").pack(anchor="w")
        self.view = ttk.Treeview(body, columns=("name", "aliases", "note"), show="headings", height=8)
        for key, title, width in (("name", "名称", 190), ("aliases", "别名", 210), ("note", "备注", 210)):
            self.view.heading(key, text=title)
            self.view.column(key, width=width)
        self.view.pack(fill="both", expand=True, pady=(8, 10))
        self.view.bind("<<TreeviewSelect>>", self._select)
        form = ttk.Frame(body)
        form.pack(fill="x")
        ttk.Label(form, text="名称").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="别名（每行一个）").grid(row=1, column=0, sticky="nw")
        self.alias_editor = tk.Text(form, height=3, wrap="word")
        self.alias_editor.grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="备注／依据").grid(row=2, column=0, sticky="nw")
        self.note_editor = tk.Text(form, height=3, wrap="word")
        self.note_editor.grid(row=2, column=1, sticky="ew", pady=3)
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="新增", command=self._clear).pack(side="left")
        ttk.Button(buttons, text="删除所选", command=self._delete).pack(side="left", padx=6)
        ttk.Button(buttons, text="完成", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=6)
        self._refresh()
        self.grab_set()

    def _refresh(self) -> None:
        self.view.delete(*self.view.get_children())
        for row in self.parent.store.related_parties():
            self.view.insert("", "end", iid=str(row["id"]), values=(row["name"], "、".join(json.loads(row["aliases_json"])), row["note"].replace("\n", " ")[:100]))
        self.parent.refresh_details()

    def _select(self, _event: object) -> None:
        selected = self.view.selection()
        if not selected:
            return
        row = next((item for item in self.parent.store.related_parties() if item["id"] == int(selected[0])), None)
        if row is None:
            return
        self.name_var.set(row["name"])
        self.alias_editor.delete("1.0", "end")
        self.alias_editor.insert("1.0", "\n".join(json.loads(row["aliases_json"])))
        self.note_editor.delete("1.0", "end")
        self.note_editor.insert("1.0", row["note"])

    def _clear(self) -> None:
        selected = self.view.selection()
        if selected:
            self.view.selection_remove(*selected)
        self.name_var.set("")
        self.alias_editor.delete("1.0", "end")
        self.note_editor.delete("1.0", "end")

    def _save(self) -> None:
        selected = self.view.selection()
        party_id = int(selected[0]) if selected else None
        try:
            saved_id = self.parent.store.save_related_party(self.name_var.get(), self.alias_editor.get("1.0", "end-1c").splitlines(), self.note_editor.get("1.0", "end-1c"), party_id)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self._refresh()
        self.view.selection_set(str(saved_id))

    def _delete(self) -> None:
        selected = self.view.selection()
        if selected and messagebox.askyesno("删除关联方", "确定从当前项目删除这条用户维护的关联方记录吗？", parent=self):
            self.parent.store.delete_related_party(int(selected[0]))
            self._clear()
            self._refresh()


class ReconciliationDialog(tk.Toplevel):
    """Confirm source scope and explicit statement-item/account mappings."""

    LABELS = {"balance_sheet": "资产负债表", "income_statement": "利润表"}

    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.store = parent.store
        self.plan = load_plan(self.store)
        self.sources = {row["id"]: row for row in self.store.sources()}
        self.title("核对范围 · 当前项目")
        self.geometry("940x720")
        self.minsize(760, 580)
        self.transient(parent)
        self.source_options: dict[str, dict[str, int | None]] = {}
        for kind in ("balance", *REPORT_KINDS):
            self.source_options[kind] = {"不参与": None}
            for source in self.sources.values():
                if source["kind"] == kind:
                    self.source_options[kind][f'{source["id"]}: {source["filename"]}/{source["sheet"]}'] = source["id"]
        self.entry_rows = [source for source in self.sources.values() if source["kind"] == "entry"]
        self.year_var = tk.StringVar(value=self.plan.get("year", ""))
        self.period_var = tk.StringVar(value=self.plan.get("period", ""))
        self.balance_var = tk.StringVar(value=self._display_source("balance", self.plan.get("balance_source_id")))
        self.report_vars = {kind: tk.StringVar(value=self._display_source(kind, self.plan.get(kind, {}).get("source_id"))) for kind in REPORT_KINDS}
        self.amount_vars = {kind: tk.StringVar(value="") for kind in REPORT_KINDS}
        self.amount_options: dict[str, dict[str, int]] = {kind: {} for kind in REPORT_KINDS}
        self.amount_combos: dict[str, ttk.Combobox] = {}
        self.map_kind = tk.StringVar(value="资产负债表")
        self.map_row = tk.StringVar()
        self.direction = tk.StringVar(value="debit")
        self.row_options: dict[str, tuple[int, str]] = {}
        self.mappings = [dict(item) for item in self.plan.get("mappings", [])]
        self._layout()
        for kind in REPORT_KINDS:
            self._refresh_amount_choices(kind, self.plan.get(kind, {}).get("amount_column", 1))
        self._refresh_rows()
        self._refresh_mappings()
        self.grab_set()

    def _display_source(self, kind: str, source_id: int | None) -> str:
        return next((label for label, value in self.source_options[kind].items() if value == source_id), "不参与")

    def _selected_source(self, kind: str) -> int | None:
        variable = self.balance_var if kind == "balance" else self.report_vars[kind]
        return self.source_options[kind].get(variable.get())

    def _layout(self) -> None:
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="仅比较您确认的同一期间和来源；差异不会修改或阻止查看账簿。", foreground="#555").pack(anchor="w")
        period_row = ttk.Frame(body)
        period_row.pack(fill="x", pady=(10, 5))
        ttk.Label(period_row, text="会计年度").pack(side="left")
        ttk.Entry(period_row, textvariable=self.year_var, width=8).pack(side="left", padx=(5, 18))
        ttk.Label(period_row, text="会计期间").pack(side="left")
        ttk.Entry(period_row, textvariable=self.period_var, width=5).pack(side="left", padx=5)
        ttk.Label(period_row, text="利润表金额列也按此期间比较；累计列请先确认对应口径。", foreground="#555").pack(side="left", padx=16)
        ttk.Label(body, text="参与核对的明细来源（可多选）").pack(anchor="w")
        self.entry_list = tk.Listbox(body, selectmode="multiple", exportselection=False, height=4)
        self.entry_list.pack(fill="x", pady=(3, 8))
        for index, source in enumerate(self.entry_rows):
            self.entry_list.insert("end", f'{source["id"]}: {source["filename"]}/{source["sheet"]}')
            if source["id"] in self.plan.get("entry_source_ids", []):
                self.entry_list.selection_set(index)
        for kind, label in (("balance", "科目余额表"), ("balance_sheet", "资产负债表"), ("income_statement", "利润表")):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=11).pack(side="left")
            variable = self.balance_var if kind == "balance" else self.report_vars[kind]
            combo = ttk.Combobox(row, textvariable=variable, values=list(self.source_options[kind]), state="readonly", width=55)
            combo.pack(side="left", fill="x", expand=True)
            if kind in REPORT_KINDS:
                combo.bind("<<ComboboxSelected>>", lambda _event, report_kind=kind: (self._refresh_amount_choices(report_kind), self._refresh_rows()))
                ttk.Label(row, text="金额列").pack(side="left", padx=(12, 4))
                amount_combo = ttk.Combobox(row, textvariable=self.amount_vars[kind], state="readonly", width=19)
                amount_combo.pack(side="left")
                self.amount_combos[kind] = amount_combo
        ttk.Separator(body).pack(fill="x", pady=10)
        ttk.Label(body, text="报表项目与科目对应（不自动匹配，也不自动合并父子科目）").pack(anchor="w")
        editor = ttk.Frame(body)
        editor.pack(fill="both", expand=True, pady=5)
        left = ttk.Frame(editor)
        left.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(left)
        controls.pack(fill="x")
        ttk.Combobox(controls, textvariable=self.map_kind, values=list(self.LABELS.values()), state="readonly", width=18).pack(side="left")
        self.map_kind.trace_add("write", lambda *_: self._refresh_rows())
        self.row_combo = ttk.Combobox(controls, textvariable=self.map_row, state="readonly", width=45)
        self.row_combo.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Label(left, text="对应科目（点击可逐项多选）").pack(anchor="w", pady=(8, 2))
        self.account_rows = self.store.db.execute("SELECT key,code,name FROM accounts ORDER BY code,name").fetchall()
        self.account_list = tk.Listbox(left, selectmode="multiple", exportselection=False, height=8)
        self.account_list.pack(fill="both", expand=True)
        for row in self.account_rows:
            self.account_list.insert("end", f'{row["code"]} {row["name"]}'.strip())
        action = ttk.Frame(left)
        action.pack(fill="x", pady=(5, 0))
        ttk.Label(action, text="取值方向").pack(side="left")
        ttk.Radiobutton(action, text="借方－贷方", variable=self.direction, value="debit").pack(side="left", padx=6)
        ttk.Radiobutton(action, text="贷方－借方", variable=self.direction, value="credit").pack(side="left", padx=6)
        ttk.Button(action, text="添加／更新对应", command=self._add_mapping).pack(side="right")
        right = ttk.Frame(editor)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        ttk.Label(right, text="已确认对应").pack(anchor="w")
        self.mapping_view = ttk.Treeview(right, columns=("report", "item", "accounts", "direction"), show="headings", height=11)
        for key, title, width in (("report", "报表", 95), ("item", "项目／原行", 165), ("accounts", "科目", 210), ("direction", "方向", 85)):
            self.mapping_view.heading(key, text=title)
            self.mapping_view.column(key, width=width, stretch=key == "accounts")
        self.mapping_view.pack(fill="both", expand=True)
        self.mapping_view.bind("<<TreeviewSelect>>", self._choose_mapping)
        ttk.Button(right, text="删除所选对应", command=self._remove_mapping).pack(anchor="e", pady=(5, 0))
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="保存并核对", command=self._save).pack(side="right")

    def _refresh_rows(self) -> None:
        kind = next((code for code, label in self.LABELS.items() if label == self.map_kind.get()), "balance_sheet")
        source_id = self._selected_source(kind)
        self.row_options = {}
        if source_id is not None:
            for row in self.store.report_rows(source_id):
                for side in (("left", "right") if kind == "balance_sheet" else ("left",)):
                    item = row[f"{side}_item"]
                    if item:
                        label = f'{row["row_no"]} 行 · {"左" if side == "left" else "右"} · {item}'
                        self.row_options[label] = (row["row_no"], side)
        self.row_combo.configure(values=list(self.row_options))
        if self.map_row.get() not in self.row_options:
            self.map_row.set(next(iter(self.row_options), ""))

    def _refresh_amount_choices(self, kind: str, preferred: int | None = None) -> None:
        source_id = self._selected_source(kind)
        options: dict[str, int] = {}
        if source_id is not None:
            metadata = json.loads(self.sources[source_id]["mapping_json"])
            fields, headers = metadata.get("mapping", {}), metadata.get("headers", [])
            for number in (1, 2, 3):
                index = fields.get(f"left_amount_{number}")
                if index is not None:
                    header = headers[index] if index < len(headers) else ""
                    options[f"{number}：{header or '金额'}"] = number
        self.amount_options[kind] = options
        self.amount_combos[kind].configure(values=list(options))
        desired = preferred if preferred is not None else self.amount_options[kind].get(self.amount_vars[kind].get(), 1)
        self.amount_vars[kind].set(next((label for label, number in options.items() if number == desired), next(iter(options), "")))

    def _refresh_mappings(self) -> None:
        self.mapping_view.delete(*self.mapping_view.get_children())
        for index, mapping in enumerate(self.mappings):
            source = self.sources.get(mapping.get("source_id"))
            item = f'{mapping.get("row_no")} 行 · {mapping.get("side")}'
            if source:
                row = next((r for r in self.store.report_rows(source["id"]) if r["row_no"] == mapping.get("row_no")), None)
                if row:
                    item += f' · {row[mapping["side"] + "_item"]}'
            names = []
            for account in self.account_rows:
                if account["key"] in mapping.get("accounts", []):
                    names.append(account["code"] or account["name"])
            self.mapping_view.insert("", "end", iid=str(index), values=(self.LABELS.get(mapping["kind"], mapping["kind"]), item, ", ".join(names), "借－贷" if mapping["direction"] == "debit" else "贷－借"))

    def _add_mapping(self) -> None:
        kind = next((code for code, label in self.LABELS.items() if label == self.map_kind.get()), "balance_sheet")
        source_id = self._selected_source(kind)
        position = self.row_options.get(self.map_row.get())
        accounts = [self.account_rows[index]["key"] for index in self.account_list.curselection()]
        if source_id is None or position is None or not accounts:
            messagebox.showinfo("对应关系不完整", "请选择报表、项目行和至少一个科目。", parent=self)
            return
        row_no, side = position
        item = {"kind": kind, "source_id": source_id, "row_no": row_no, "side": side, "accounts": accounts, "direction": self.direction.get()}
        self.mappings = [old for old in self.mappings if (old.get("kind"), old.get("source_id"), old.get("row_no"), old.get("side")) != (kind, source_id, row_no, side)]
        self.mappings.append(item)
        self._refresh_mappings()

    def _choose_mapping(self, _event: object) -> None:
        selection = self.mapping_view.selection()
        if not selection:
            return
        mapping = self.mappings[int(selection[0])]
        self.map_kind.set(self.LABELS[mapping["kind"]])
        self._refresh_rows()
        target = next((label for label, position in self.row_options.items() if position == (mapping["row_no"], mapping["side"])), "")
        self.map_row.set(target)
        self.account_list.selection_clear(0, "end")
        for index, account in enumerate(self.account_rows):
            if account["key"] in mapping["accounts"]:
                self.account_list.selection_set(index)
        self.direction.set(mapping["direction"])

    def _remove_mapping(self) -> None:
        selection = self.mapping_view.selection()
        if selection:
            del self.mappings[int(selection[0])]
            self._refresh_mappings()

    def _save(self) -> None:
        plan = empty_plan()
        plan["year"], plan["period"] = self.year_var.get().strip(), self.period_var.get().strip()
        plan["entry_source_ids"] = [self.entry_rows[index]["id"] for index in self.entry_list.curselection()]
        plan["balance_source_id"] = self._selected_source("balance")
        for kind in REPORT_KINDS:
            plan[kind] = {"source_id": self._selected_source(kind), "amount_column": self.amount_options[kind].get(self.amount_vars[kind].get(), 1)}
        plan["mappings"] = [mapping for mapping in self.mappings if mapping.get("source_id") == plan[mapping["kind"]]["source_id"]]
        try:
            save_plan(self.store, plan)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.destroy()
        self.parent.refresh_all()


class NoteDialog(tk.Toplevel):
    def __init__(self, parent: "App", entry_id: int | None = None, key: str | None = None):
        super().__init__(parent)
        self.parent = parent
        self.entry_id = entry_id
        existing = mark_for_entry(parent.store, entry_id) if entry_id is not None else parent.store.db.execute("SELECT * FROM voucher_marks WHERE voucher_key=?", (key,)).fetchone()
        self.key = existing["voucher_key"] if existing else key
        self.title("关注凭证与注释")
        self.geometry("620x460")
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="注释保存在当前项目；再次导出 MD 会使用这里的内容。", foreground="#555").pack(anchor="w")
        if key:
            status = next((item["status"] for item in list_marks(parent.store) if item["key"] == key), "未匹配")
            ttk.Label(body, text=f"当前匹配状态：{status}").pack(anchor="w", pady=(6, 0))
        current_key = self.key or voucher_key(parent.store.entry(entry_id))
        lines = voucher_lines_for_key(parent.store, current_key)
        if lines:
            preview = tk.Text(body, height=6, wrap="none", state="normal", font="TkFixedFont")
            preview.pack(fill="x", pady=(8, 0))
            for line in lines:
                code, name = source_account(line)
                preview.insert("end", f'{line["entry_date"]} {line["voucher_type"]}{line["voucher_no"]}  {code} {name}  {line["summary"]}  借 {amount(line["debit_cents"]) or "0.00"}  贷 {amount(line["credit_cents"]) or "0.00"}\n')
            preview.configure(state="disabled")
        else:
            ttk.Label(body, text="原凭证已无法匹配；注释仍保存在项目中。", foreground="#995300").pack(anchor="w", pady=(8, 0))
        self.editor = tk.Text(body, wrap="word", undo=True)
        self.editor.pack(fill="both", expand=True, pady=10)
        if existing:
            self.editor.insert("1.0", existing["note"])
        footer = ttk.Frame(body)
        footer.pack(fill="x")
        if existing:
            ttk.Button(footer, text="取消关注", command=self._remove).pack(side="left")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="保存关注与注释", command=self._save).pack(side="right")
        self.grab_set()

    def _save(self) -> None:
        note = self.editor.get("1.0", "end-1c")
        try:
            if self.entry_id is not None:
                save_mark(self.parent.store, self.entry_id, note)
            else:
                save_mark_by_key(self.parent.store, self.key, note)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.destroy()
        self.parent.refresh_all()

    def _remove(self) -> None:
        if self.key and messagebox.askyesno("取消关注", "确定取消关注并删除这条注释吗？", parent=self):
            remove_mark(self.parent.store, self.key)
            self.destroy()
            self.parent.refresh_all()


class ImportDialog(tk.Toplevel):
    def __init__(self, parent: "App", path: Path):
        super().__init__(parent)
        self.parent = parent
        self.path = path
        self.title("导入资料 · 核对每列含义")
        self.geometry("960x780")
        self.transient(parent)
        self.grab_set()
        self.sheet = tk.StringVar(value=sheet_names(path)[0])
        self.kind = tk.StringVar(value="明细账／序时账")
        self.header = tk.StringVar(value="1")
        self.columns: list[str] = []
        self.vars: dict[str, tk.StringVar] = {}
        self._jev_queue: Queue = Queue()
        self._jev_sheet = ""
        self._layout()
        self._auto_kind_for_sheet()
        self.refresh_preview()

    def _auto_kind_for_sheet(self) -> None:
        spec = classify_sheet(self.path, self.sheet.get())
        if spec is not None:
            self.kind.set({"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}[spec.kind])

    def _layout(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text=self.path.name, font=("", 14, "bold")).pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="工作表").pack(side="left")
        sheet = ttk.Combobox(row, textvariable=self.sheet, values=sheet_names(self.path), state="readonly", width=25)
        sheet.pack(side="left", padx=(6, 18))
        sheet.bind("<<ComboboxSelected>>", lambda _e: (self._auto_kind_for_sheet(), self.refresh_preview()))
        ttk.Label(row, text="表类型").pack(side="left")
        kind = ttk.Combobox(row, textvariable=self.kind, values=("明细账／序时账", "科目余额表", "资产负债表", "利润表"), state="readonly", width=16)
        kind.pack(side="left", padx=(6, 18))
        kind.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        ttk.Label(row, text="表头行号").pack(side="left")
        ttk.Entry(row, textvariable=self.header, width=5).pack(side="left", padx=6)
        ttk.Button(row, text="按行号读取表头", command=lambda: self.refresh_preview(manual=True)).pack(side="left", padx=5)
        ttk.Label(top, text="先看原表，再确认每个字段对应的列。〔空〕与原表的 0 分开显示。", foreground="#555").pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(self, height=5, wrap="none", state="disabled", font="TkFixedFont")
        self.preview.pack(fill="x", padx=12, pady=8)
        bottom = ttk.Frame(self, padding=12)
        bottom.pack(side="bottom", fill="x")
        self.import_note = ttk.Label(bottom, text="账簿空白/小计行会跳过；请确认借贷列不是余额列。", foreground="#995300")
        self.import_note.pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="确认并导入", command=self.import_now).pack(side="right", padx=4)
        self.jev_button = ttk.Button(bottom, text="Jev 建议列", command=self._offer_jev)
        self.jev_button.pack(side="right", padx=8)
        map_holder = ttk.Frame(self)
        map_holder.pack(fill="both", expand=True, padx=12, pady=4)
        self.map_canvas = tk.Canvas(map_holder, highlightthickness=0, background="#F5F8F7")
        map_scroll = ttk.Scrollbar(map_holder, orient="vertical", command=self.map_canvas.yview)
        self.map_canvas.configure(yscrollcommand=map_scroll.set)
        self.map_canvas.pack(side="left", fill="both", expand=True)
        map_scroll.pack(side="right", fill="y")
        self.map_frame = ttk.LabelFrame(self.map_canvas, text="每一列的含义 · 可滚动", padding=12)
        self.map_window = self.map_canvas.create_window((0, 0), window=self.map_frame, anchor="nw")
        self.map_frame.bind("<Configure>", lambda _event: self.map_canvas.configure(scrollregion=self.map_canvas.bbox("all")))
        self.map_canvas.bind("<Configure>", lambda event: self.map_canvas.itemconfigure(self.map_window, width=event.width))

    def _offer_jev(self) -> None:
        try:
            api_key = get_api_key()
            if not api_key:
                self.grab_release()
                settings = SettingsDialog(self.parent)
                settings.lift()
                settings.grab_set()
                self.wait_window(settings)
                if not self.winfo_exists():
                    return
                self.grab_set()
                api_key = get_api_key()
                if not api_key:
                    return
            sheet = self.sheet.get()
            preview = preview_sheet(self.path, sheet)
        except Exception as exc:
            messagebox.showerror("无法取得建议", str(exc), parent=self)
            return
        description = (
            f"将工作表“{sheet}”的前 {len(preview['rows'])} 行发送给 Jev；"
            "每行最多 20 列，每格最多 60 字。预览可能含主体名称和金额。"
            "不发送整份文件或本地路径。是否继续？"
        )
        if not messagebox.askokcancel("确认发送预览", description, parent=self):
            return
        self._jev_sheet = sheet
        self.jev_button.configure(state="disabled", text="Jev 正在分析…")

        def worker() -> None:
            try:
                self._jev_queue.put((True, suggest_mapping(preview, api_key)))
            except Exception as exc:
                self._jev_queue.put((False, str(exc).replace(api_key, "[密钥已隐藏]")))

        Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_jev)

    def _poll_jev(self) -> None:
        try:
            success, result = self._jev_queue.get_nowait()
        except Empty:
            self.after(120, self._poll_jev)
            return
        self.jev_button.configure(state="normal", text="Jev 建议列")
        if not success:
            messagebox.showerror("Jev 建议未完成", result, parent=self)
            return
        if self.sheet.get() != self._jev_sheet:
            messagebox.showinfo("工作表已切换", "刚才的建议属于原工作表，未应用到当前表。", parent=self)
            return
        self.kind.set({"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}[result.kind])
        self.header.set(str(result.header_row))
        self.refresh_preview(manual=True)
        for variable in self.vars.values():
            variable.set("不导入")
        for field, index in result.mapping.items():
            if field in self.vars and index < len(self.columns):
                self.vars[field].set(self.columns[index])
        self.import_note.configure(text="已填入 Jev 建议；请对照原表逐列确认后再导入。")
        caution = "\n".join(result.warnings[:8]) or "所有建议均需按原表核对，尤其是期间和借贷方向。"
        messagebox.showinfo("Jev 建议已填入", f"模型：{result.model}。表类型、表头行和列映射已填入下方；请逐项核对后再点“确认并导入”。\n{caution}", parent=self)

    def refresh_preview(self, manual: bool = False) -> None:
        try:
            if manual:
                row_no = int(self.header.get())
                if row_no < 1:
                    raise ValueError("表头行号必须大于 0")
                headers = header_for_row(self.path, self.sheet.get(), row_no, self.kind_code())
                guess = guess_mapping(headers, self.kind_code())
            else:
                row_no, headers, guess = guess_header(self.path, self.sheet.get(), self.kind_code())
            if not headers:
                raise ValueError("找不到表头")
            self.header.set(str(row_no))
            self.columns = [f"{i+1}: {name or '(空列)'}" for i, name in enumerate(headers)]
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            for number, line in rows(self.path, self.sheet.get()):
                self.preview.insert("end", f"{number:>4}  " + " | ".join((text_value(value)[:26] if value is not None and str(value).strip() else "〔空〕") for value in line[:14]) + "\n")
                if number >= row_no + 5:
                    break
            self.preview.configure(state="disabled")
            for child in self.map_frame.winfo_children():
                child.destroy()
            self.vars.clear()
            kind_code = self.kind_code()
            if kind_code in REPORT_KINDS:
                fields = ("left_item", "left_amount_1", "left_amount_2", "left_amount_3", "right_item", "right_amount_1", "right_amount_2", "right_amount_3") if kind_code == "balance_sheet" else ("left_item", "left_amount_1", "left_amount_2", "left_amount_3")
                self.import_note.configure(text="报表保留原行序、标题和小计；空白金额与零值分开保存，不做勾稽核对。")
            else:
                fields = ("date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_code", "account_name", "summary", "debit", "credit", "counterparty", "auxiliary", "currency", "original_amount", "maker") if kind_code == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "closing_balance", "direction")
                self.import_note.configure(text="账簿空白/小计行会跳过；请确认借贷列不是余额列。")
            columns_per_row = 1 if self.parent.controller.font_size >= 17 else 2
            for n, field in enumerate(fields):
                r, c = divmod(n, columns_per_row)
                cell = ttk.Frame(self.map_frame)
                cell.grid(row=r, column=c, sticky="ew", padx=6, pady=8)
                ttk.Label(cell, text=FIELD_LABELS[field], width=11).pack(side="left")
                selected = self.columns[guess[field]] if field in guess else "不导入"
                variable = tk.StringVar(value=selected)
                self.vars[field] = variable
                ttk.Combobox(cell, textvariable=variable, values=["不导入", *self.columns], state="readonly", width=28).pack(side="left", fill="x", expand=True)
            self.map_frame.columnconfigure(0, weight=1)
            self.map_frame.columnconfigure(1, weight=1)
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)

    def kind_code(self) -> str:
        if self.kind.get() == "明细账／序时账":
            return "entry"
        if self.kind.get() == "科目余额表":
            return "balance"
        return "balance_sheet" if self.kind.get() == "资产负债表" else "income_statement"

    def import_now(self) -> None:
        try:
            mapping = {field: self.columns.index(var.get()) for field, var in self.vars.items() if var.get() != "不导入"}
            spec = ImportSpec(self.path, self.sheet.get(), int(self.header.get()), self.kind_code(), mapping)
            count = self.parent.store.import_sheet(spec)
        except sqlite3.IntegrityError:
            if not messagebox.askyesno("重新导入", "同一来源工作表已有导入记录。是否用当前列映射替换旧导入记录？原始 Excel 不会修改。", parent=self):
                return
            try:
                count = self.parent.store.import_sheet(spec, replace=True)
            except Exception as exc:
                messagebox.showerror("替换失败", f"旧导入记录已保留。\n{exc}", parent=self)
                return
        except Exception as exc:
            messagebox.showerror("导入失败", f"没有写入本次数据。\n{exc}", parent=self)
            return
        self.destroy()
        self.parent.refresh_all()
        differences = sum(item.level == "差异" for item in run_checks(self.parent.store))
        tail = f"发现 {differences} 项差异，请查看“核对提醒”页。" if differences else "没有发现已确认口径下的金额差异；待核事项请查看“核对提醒”页。"
        messagebox.showinfo("导入完成", f"成功导入 {count} 行；{tail}", parent=self.parent)


class BatchImportDialog(tk.Toplevel):
    LABELS = {"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}

    def __init__(self, parent: "App", paths: list[Path]):
        super().__init__(parent)
        self.parent = parent
        self.specs, self.skipped = discover_sheets(paths)
        self.title("批量导入 · 确认自动分类")
        self.geometry("760x470")
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="已按工作表名称和表头自动分类。点击条目可取消或重新选择；未确认的表不会自动导入。", foreground="#555").pack(anchor="w")
        self.listbox = tk.Listbox(body, selectmode="multiple", exportselection=False, height=11)
        self.listbox.pack(fill="both", expand=True, pady=(8, 0))
        for index, spec in enumerate(self.specs):
            self.listbox.insert("end", f'{self.LABELS[spec.kind]}  ·  {spec.path.name}/{spec.sheet}  ·  表头第 {spec.header_row} 行')
            self.listbox.selection_set(index)
        if self.skipped:
            ttk.Label(body, text="待单独核对（不自动导入）：", foreground="#995300").pack(anchor="w", pady=(10, 0))
            skipped_view = tk.Text(body, height=min(5, len(self.skipped)), wrap="word", font="TkTextFont")
            skipped_view.pack(fill="x")
            skipped_view.insert("1.0", "\n".join(self.skipped))
            skipped_view.configure(state="disabled")
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="导入选中的工作表", command=self._import_selected).pack(side="right")
        self.grab_set()

    def _import_selected(self) -> None:
        selected = [self.specs[index] for index in self.listbox.curselection()]
        if not selected:
            messagebox.showinfo("未选择表", "请至少选择一个已分类的工作表。", parent=self)
            return
        old_sources = self.parent.store.sources()
        duplicate = set()
        failures = []
        unreadable = set()
        for index, spec in enumerate(selected):
            try:
                digest = file_hash(spec.path)
            except OSError as exc:
                failures.append(f'{spec.path.name}/{spec.sheet}：文件无法读取（{exc}）')
                unreadable.add(index)
                continue
            if any(source["kind"] == spec.kind and source["sheet"] == spec.sheet and (source["sha256"] == digest or source["original_path"] == str(spec.path.resolve())) for source in old_sources):
                duplicate.add(index)
        replace = False
        if duplicate:
            replace = messagebox.askyesno("已有导入记录", f"{len(duplicate)} 个工作表已有导入记录。是否替换这些记录？选择“否”将跳过它们，继续导入其他表。", parent=self)
        imported = []
        for index, spec in enumerate(selected):
            if index in unreadable or (index in duplicate and not replace):
                continue
            try:
                count = self.parent.store.import_sheet(spec, replace=index in duplicate)
                imported.append(f'{spec.path.name}/{spec.sheet}：{count} 行')
            except Exception as exc:
                failures.append(f'{spec.path.name}/{spec.sheet}：{exc}')
        self.parent.refresh_all()
        self.destroy()
        differences = sum(item.level == "差异" for item in run_checks(self.parent.store))
        summary = f"成功导入 {len(imported)} 个工作表；当前发现 {differences} 项差异。"
        if duplicate and not replace:
            summary += f"\n跳过 {len(duplicate)} 个已有来源。"
        if failures:
            summary += "\n失败项：\n" + "\n".join(failures)
        if self.skipped:
            summary += f"\n另有 {len(self.skipped)} 个工作表未自动分类，可单独导入。"
        messagebox.showinfo("批量导入结果", summary, parent=self.parent)


class App(tk.Toplevel):
    def __init__(self, controller: "Application"):
        super().__init__(controller)
        self.controller = controller
        self.title(f"账簿核查 · v{__version__}")
        self.geometry("920x600")
        self.minsize(720, 480)
        self.resizable(True, True)
        self.store: LedgerStore | None = None
        self.account: str | None = None
        self.account_label = tk.StringVar(value="全部科目")
        self.period_filter = tk.StringVar(value="全部期间")
        self.period_options: dict[str, tuple[str, str]] = {}
        self.balance_source = tk.StringVar(value="无余额表")
        self.balance_options: dict[str, int] = {}
        self.report_source = {kind: tk.StringVar(value="无报表") for kind in REPORT_KINDS}
        self.report_options: dict[str, dict[str, int]] = {kind: {} for kind in REPORT_KINDS}
        self.search = tk.StringVar()
        self.mark_search = tk.StringVar()
        self.mark_keys: dict[str, str] = {}
        self.status = tk.StringVar(value="先新建或打开一个本地项目。")
        self.column_views: dict[str, list[ttk.Treeview]] = {kind: [] for kind in VIEW_COLUMNS}
        self.voucher_windows: set[tk.Toplevel] = set()
        self._layout()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _layout(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 7))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="账簿核查", style="Intro.TLabel").pack(anchor="w")
        ttk.Label(toolbar, text="导入资料  →  查看科目与分录  →  记录需要关注的凭证", style="Hint.TLabel").pack(anchor="w", pady=(1, 7))
        file_actions = ttk.Frame(toolbar)
        file_actions.pack(fill="x")
        ttk.Button(file_actions, text="新建项目", command=self.new_book).pack(side="left", padx=(0, 6))
        ttk.Button(file_actions, text="打开项目", command=self.open_book).pack(side="left", padx=(0, 6))
        ttk.Button(file_actions, text="设置", command=self.open_settings).pack(side="right")
        import_actions = ttk.Frame(toolbar)
        import_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(import_actions, text="导入一份资料", command=self.import_file).pack(side="left", padx=(0, 6))
        ttk.Button(import_actions, text="导入多份资料", command=self.import_files).pack(side="left")
        search_row = ttk.Frame(toolbar)
        search_row.pack(fill="x", pady=(6, 0))
        ttk.Label(search_row, text="搜索").pack(side="left", padx=(0, 6))
        entry = ttk.Entry(search_row, textvariable=self.search)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.refresh_all())
        ttk.Button(search_row, text="查找", command=self.refresh_all).pack(side="left", padx=6)
        ttk.Button(search_row, text="清空", command=self.clear_filter).pack(side="left")
        ttk.Button(search_row, text="关联方名单", command=self.open_related_parties).pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=4)
        frames = [ttk.Frame(self.tabs) for _ in range(8)]
        for frame, title in zip(frames, ("科目总览", "明细记录", "资产负债表", "利润表", "往来线索", "核对提醒", "导入记录", "关注清单")):
            self.tabs.add(frame, text=title)
        balance_top = ttk.Frame(frames[0], padding=5)
        balance_top.pack(fill="x")
        ttk.Label(balance_top, text="余额表来源").grid(row=0, column=0, sticky="w")
        self.balance_combo = ttk.Combobox(balance_top, textvariable=self.balance_source, values=("无余额表",), state="readonly", width=28)
        self.balance_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.balance_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())
        ttk.Label(balance_top, text="不同期间的期末余额不相加", foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(balance_top, text="显示／隐藏列", command=lambda: self._open_columns(self, "balance")).grid(row=1, column=2, padx=(12, 0), pady=(4, 0))
        balance_top.columnconfigure(1, weight=1)
        self.summary_view = tree(frames[0], VIEW_COLUMNS["balance"])
        self._register_column_view("balance", self.summary_view)
        self.summary_view.bind("<Double-1>", self.open_account)
        detail_top = ttk.Frame(frames[1], padding=5)
        detail_top.pack(fill="x")
        ttk.Label(detail_top, textvariable=self.account_label).pack(fill="x", anchor="w")
        detail_controls = ttk.Frame(detail_top)
        detail_controls.pack(fill="x", pady=(4, 0))
        detail_buttons = ttk.Frame(detail_controls)
        detail_buttons.pack(fill="x")
        ttk.Button(detail_buttons, text="关注／注释所选凭证", command=self.mark_selected_entry).pack(side="left")
        ttk.Button(detail_buttons, text="显示／隐藏列", command=lambda: self._open_columns(self, "detail")).pack(side="left", padx=6)
        ttk.Button(detail_buttons, text="显示全部科目", command=self.show_all_entries).pack(side="right")
        period_controls = ttk.Frame(detail_controls)
        period_controls.pack(fill="x", pady=(4, 0))
        ttk.Button(period_controls, text="导出当前明细 CSV", command=self.export_csv).pack(side="left")
        self.period_combo = ttk.Combobox(period_controls, textvariable=self.period_filter, values=("全部期间",), state="readonly", width=17)
        self.period_combo.pack(side="right", padx=(5, 18))
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._period_changed())
        ttk.Label(period_controls, text="会计期间").pack(side="right")
        ttk.Label(detail_controls, text="借方·灰蓝  贷方·暖灰", style="Hint.TLabel").pack(anchor="w", pady=(3, 0))
        self.detail_view = tree(frames[1], VIEW_COLUMNS["detail"])
        self._register_column_view("detail", self.detail_view)
        self.detail_view.bind("<Double-1>", self.open_voucher)
        self.report_views: dict[str, ttk.Treeview] = {}
        self.report_info: dict[str, tk.StringVar] = {}
        self.report_combos: dict[str, ttk.Combobox] = {}
        for kind, frame in (("balance_sheet", frames[2]), ("income_statement", frames[3])):
            top = ttk.Frame(frame, padding=5)
            top.pack(fill="x")
            ttk.Label(top, text="报表来源").pack(side="left")
            combo = ttk.Combobox(top, textvariable=self.report_source[kind], values=("无报表",), state="readonly", width=40)
            combo.pack(side="left", fill="x", expand=True, padx=8)
            self.report_combos[kind] = combo
            combo.bind("<<ComboboxSelected>>", lambda _e, report_kind=kind: self.refresh_report(report_kind))
            info = tk.StringVar(value="尚未导入报表。")
            self.report_info[kind] = info
            ttk.Label(frame, textvariable=info, padding=(8, 3), foreground="#555").pack(fill="x")
            columns = [("left_item", "项目", 230), ("left_a", "金额 1", 125), ("left_b", "金额 2", 125), ("left_c", "金额 3", 125)]
            if kind == "balance_sheet":
                columns += [("right_item", "右侧项目", 230), ("right_a", "右侧金额 1", 125), ("right_b", "右侧金额 2", 125), ("right_c", "右侧金额 3", 125)]
            columns += [("source", "来源行", 260)]
            self.report_views[kind] = tree(frame, columns)
        self.aux_view = tree(frames[4], [("label", "辅助项/往来单位", 280), ("debit", "借方合计", 150), ("credit", "贷方合计", 150), ("n", "行数", 90)])
        check_top = ttk.Frame(frames[5], padding=5)
        check_top.pack(fill="x")
        ttk.Label(check_top, text="这里只列需要核对的线索；金额差异不影响查看原始分录。", style="Hint.TLabel").pack(side="left")
        ttk.Button(check_top, text="设置核对范围", command=self.open_reconciliation).pack(side="right")
        self.check_view = tree(frames[5], [("level", "级别", 70), ("category", "类别", 130), ("subject", "对象", 250), ("detail", "说明", 650)])
        self.source_view = tree(frames[6], [("file", "文件", 220), ("sheet", "工作表", 130), ("kind", "类型", 85), ("rows", "有效行", 80), ("hash", "SHA-256", 220), ("path", "原始路径", 430)])
        mark_top = ttk.Frame(frames[7], padding=5)
        mark_top.pack(fill="x")
        mark_search_row = ttk.Frame(mark_top)
        mark_search_row.pack(fill="x")
        ttk.Label(mark_search_row, text="查找").pack(side="left")
        mark_entry = ttk.Entry(mark_search_row, textvariable=self.mark_search)
        mark_entry.pack(side="left", fill="x", expand=True, padx=5)
        mark_entry.bind("<Return>", lambda _event: self.refresh_marks())
        ttk.Button(mark_search_row, text="筛选", command=self.refresh_marks).pack(side="left")
        mark_actions = ttk.Frame(mark_top)
        mark_actions.pack(fill="x", pady=(5, 0))
        ttk.Button(mark_actions, text="编辑注释", command=self.edit_selected_mark).pack(side="left")
        ttk.Button(mark_actions, text="取消关注", command=self.remove_selected_mark).pack(side="left", padx=5)
        ttk.Button(mark_actions, text="另存为 MD", command=lambda: self.export_marks(as_new=True)).pack(side="right")
        ttk.Button(mark_actions, text="一键更新 MD", command=self.export_marks).pack(side="right", padx=5)
        self.mark_view = tree(frames[7], [("status", "状态", 85), ("voucher", "凭证", 175), ("accounts", "涉及科目", 275), ("note", "注释摘要", 360)])
        self.mark_view.bind("<Double-1>", lambda _event: self.edit_selected_mark())
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(12, 6)).pack(fill="x")

    def _register_column_view(self, kind: str, view: ttk.Treeview) -> None:
        self.column_views[kind].append(view)
        saved = self.store.display_columns(kind) if self.store else None
        view.configure(displaycolumns=visible_columns(kind, saved))
        view.dashed_grid.schedule()

    def _change_columns(self, kind: str, selected: list[str]) -> None:
        if self.store:
            self.store.save_display_columns(kind, selected)
        self.column_views[kind] = [view for view in self.column_views[kind] if view.winfo_exists()]
        for view in self.column_views[kind]:
            view.configure(displaycolumns=selected)
            view.dashed_grid.schedule()

    def _open_columns(self, parent: tk.Widget, kind: str) -> None:
        if not self.store:
            return
        ColumnDialog(parent, kind, visible_columns(kind, self.store.display_columns(kind)), lambda selected: self._change_columns(kind, selected))

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_reconciliation(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        ReconciliationDialog(self)

    def open_related_parties(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        RelatedPartiesDialog(self)

    def _close(self) -> None:
        for dialog in list(self.voucher_windows):
            if dialog.winfo_exists():
                dialog.destroy()
        if self.store:
            self.store.close()
            self.controller.book_windows.pop(self.store.path.resolve(), None)
        self.destroy()
        self.controller.windows.discard(self)
        if not self.controller.windows:
            self.controller.destroy()

    def new_book(self) -> None:
        chosen = filedialog.asksaveasfilename(parent=self, title="新建项目文件", defaultextension=".sqlite", filetypes=[("项目文件", "*.sqlite")])
        if chosen:
            if Path(chosen).exists() and Path(chosen).stat().st_size:
                messagebox.showwarning("文件已存在", "请选择新的文件名；打开已有项目请用“打开项目”。", parent=self)
                return
            self.controller.open_book(Path(chosen), self)

    def open_book(self) -> None:
        chosen = filedialog.askopenfilename(parent=self, title="打开项目文件", filetypes=[("项目文件", "*.sqlite"), ("所有文件", "*")])
        if chosen:
            self.controller.open_book(Path(chosen), self)

    def _use_book(self, path: Path) -> None:
        try:
            new_store = LedgerStore(path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)
            return
        self.store = new_store
        for kind, views in self.column_views.items():
            self.column_views[kind] = [view for view in views if view.winfo_exists()]
            for view in self.column_views[kind]:
                view.configure(displaycolumns=visible_columns(kind, self.store.display_columns(kind)))
        self.account = None
        self.account_label.set("全部科目")
        self.period_filter.set("全部期间")
        self.balance_source.set("无余额表")
        for choice in self.report_source.values():
            choice.set("无报表")
        self.title(f"账簿核查 · {path.name}")
        self.refresh_all()

    def import_file(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        chosen = filedialog.askopenfilename(parent=self, title="选择企业账簿", filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.xls *.csv"), ("所有文件", "*")])
        if chosen:
            try:
                ImportDialog(self, Path(chosen))
            except Exception as exc:
                messagebox.showerror("读取失败", str(exc), parent=self)

    def import_files(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        chosen = filedialog.askopenfilenames(parent=self, title="选择要一起导入的表格文件", filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.xls *.csv"), ("所有文件", "*")])
        if chosen:
            BatchImportDialog(self, [Path(path) for path in chosen])

    def export_csv(self) -> None:
        if not self.store:
            return
        chosen = filedialog.asksaveasfilename(parent=self, title="导出当前科目明细", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if chosen:
            try:
                year, period = self._selected_period()
                count = self.store.export_entries(Path(chosen), self.account, self.search.get().strip(), year, period)
                messagebox.showinfo("导出完成", f"已导出 {count} 行。", parent=self)
            except Exception as exc:
                messagebox.showerror("导出失败", str(exc), parent=self)

    def mark_selected_entry(self) -> None:
        if not self.store:
            return
        selection = self.detail_view.selection()
        if not selection:
            messagebox.showinfo("请选择分录", "请先在“明细记录”中选中一行。", parent=self)
            return
        NoteDialog(self, entry_id=int(selection[0]))

    def refresh_marks(self) -> None:
        self.mark_view.delete(*self.mark_view.get_children())
        self.mark_keys = {}
        if not self.store:
            return
        needle = self.mark_search.get().strip().lower()
        for index, mark in enumerate(list_marks(self.store)):
            lines = mark["lines"]
            if lines:
                first = lines[0]
                label = f'{first["fiscal_year"]}年{first["period"]}期 {first["voucher_type"]}{first["voucher_no"]}' if first["voucher_no"] else f'{first["entry_date"]} 无凭证号'
                accounts = ", ".join(dict.fromkeys((source_account(line)[0] or source_account(line)[1]) for line in lines))
            else:
                label, accounts = "原凭证未匹配", ""
            if needle and needle not in f'{label} {accounts} {mark["note"]} {mark["status"]}'.lower():
                continue
            iid = str(index)
            self.mark_keys[iid] = mark["key"]
            self.mark_view.insert("", "end", iid=iid, values=(mark["status"], label, accounts, mark["note"].replace("\n", " ")[:160]))

    def _selected_mark_key(self) -> str | None:
        selection = self.mark_view.selection()
        return self.mark_keys.get(selection[0]) if selection else None

    def edit_selected_mark(self) -> None:
        key = self._selected_mark_key()
        if key:
            NoteDialog(self, key=key)

    def remove_selected_mark(self) -> None:
        key = self._selected_mark_key()
        if key and self.store and messagebox.askyesno("取消关注", "确定取消关注并删除这条注释吗？", parent=self):
            remove_mark(self.store, key)
            self.refresh_all()

    def export_marks(self, as_new: bool = False) -> None:
        if not self.store:
            return
        path_text = "" if as_new else self.store.setting("marked_export_path")
        if not path_text:
            chosen = filedialog.asksaveasfilename(parent=self, title="保存关注凭证 MD", defaultextension=".md", filetypes=[("Markdown", "*.md")])
            if not chosen:
                return
            path_text = chosen
        try:
            count, skipped = export_marks_md(self.store, Path(path_text))
            self.store.save_setting("marked_export_path", str(Path(path_text).resolve()))
            messagebox.showinfo("导出完成", f"已更新 {path_text}\n导出 {count} 张凭证；{skipped} 张待复核未导出。", parent=self)
        except Exception as exc:
            messagebox.showerror("导出失败", f"原 MD 文件已保留。\n{exc}", parent=self)

    def clear_filter(self) -> None:
        self.search.set("")
        self.refresh_all()

    def show_all_entries(self) -> None:
        self.account = None
        self.account_label.set("全部科目")
        self.refresh_details()
        self._refresh_aux()

    def _selected_period(self) -> tuple[str | None, str | None]:
        return self.period_options.get(self.period_filter.get(), (None, None))

    def _period_changed(self) -> None:
        self.refresh_details()
        self._refresh_aux()

    def _refresh_aux(self) -> None:
        self.aux_view.delete(*self.aux_view.get_children())
        if not self.store:
            return
        year, period = self._selected_period()
        for row in self.store.auxiliary_totals(self.account, year, period):
            self.aux_view.insert("", "end", values=(row["label"], amount(row["debit"]), amount(row["credit"]), row["n"]))

    def refresh_all(self) -> None:
        for view in (self.summary_view, self.detail_view, self.aux_view, self.check_view, self.source_view, self.mark_view):
            view.delete(*view.get_children())
        for view in self.report_views.values():
            view.delete(*view.get_children())
        if not self.store:
            return
        self.period_options = {f"{year}年{period}期": (year, period) for year, period in self.store.periods()}
        self.period_combo.configure(values=("全部期间", *self.period_options))
        if self.period_filter.get() not in self.period_options:
            self.period_filter.set("全部期间")
        balance_sources = [source for source in self.store.sources() if source["kind"] == "balance"]
        self.balance_options = {f'{source["id"]}: {source["filename"]} / {source["sheet"]}': source["id"] for source in balance_sources}
        self.balance_combo.configure(values=tuple(self.balance_options) if self.balance_options else ("无余额表",))
        if self.balance_source.get() not in self.balance_options:
            self.balance_source.set(next(reversed(self.balance_options)) if self.balance_options else "无余额表")
        selected_balance = self.balance_options.get(self.balance_source.get())
        for row in self.store.summary(self.search.get().strip(), selected_balance):
            # When no supplied balance table exists, show computed period movement only.
            pd = row["reported_debit"] if row["balance_count"] else row["detail_debit"]
            pc = row["reported_credit"] if row["balance_count"] else row["detail_credit"]
            side_tag = "debit_row" if row["closing_debit"] and not row["closing_credit"] else "credit_row" if row["closing_credit"] and not row["closing_debit"] else ""
            self.summary_view.insert("", "end", iid=row["key"], values=(row["code"], row["name"], amount(row["opening_debit"]), amount(row["opening_credit"]), amount(pd), amount(pc), amount(row["year_debit"]), amount(row["year_credit"]), amount(row["closing_debit"]), amount(row["closing_credit"]), row["detail_count"]), tags=(side_tag,) if side_tag else ())
        self.refresh_details()
        self._refresh_aux()
        for kind in REPORT_KINDS:
            report_sources = [source for source in self.store.sources() if source["kind"] == kind]
            self.report_options[kind] = {f'{source["id"]}: {source["filename"]} / {source["sheet"]}': source["id"] for source in report_sources}
            self.report_combos[kind].configure(values=tuple(self.report_options[kind]) if self.report_options[kind] else ("无报表",))
            if self.report_source[kind].get() not in self.report_options[kind]:
                self.report_source[kind].set(next(reversed(self.report_options[kind])) if self.report_options[kind] else "无报表")
            self.refresh_report(kind)
        for f in run_checks(self.store):
            tag = "difference" if f.level == "差异" else "pending" if f.level == "待核" else ""
            self.check_view.insert("", "end", values=(f.level, f.category, f.subject, f.detail), tags=(tag,) if tag else ())
        for row in self.store.sources():
            kind_label = {"entry": "明细", "balance": "科目余额", **REPORT_LABELS}.get(row["kind"], row["kind"])
            self.source_view.insert("", "end", values=(row["filename"], row["sheet"], kind_label, row["row_count"], row["sha256"], row["original_path"]))
        self.refresh_marks()
        self.status.set(f"当前项目：{self.store.path.name}  ·  已导入 {len(self.store.sources())} 张表  ·  双击科目看明细，双击明细看凭证")

    def refresh_report(self, kind: str) -> None:
        view = self.report_views[kind]
        view.delete(*view.get_children())
        if not self.store:
            return
        source_id = self.report_options[kind].get(self.report_source[kind].get())
        if source_id is None:
            self.report_info[kind].set("尚未导入报表。")
            return
        source = next(row for row in self.store.sources() if row["id"] == source_id)
        metadata = json.loads(source["mapping_json"])
        mapping, headers = metadata["mapping"], metadata.get("headers", [])
        def label(field: str, fallback: str) -> str:
            index = mapping.get(field)
            return headers[index] if index is not None and index < len(headers) and headers[index] else fallback
        view.heading("left_item", text=label("left_item", "项目"))
        view.heading("left_a", text=label("left_amount_1", "金额 1"))
        view.heading("left_b", text=label("left_amount_2", "金额 2"))
        view.heading("left_c", text=label("left_amount_3", "金额 3"))
        display = ["left_item", "left_a"]
        if "left_amount_2" in mapping:
            display.append("left_b")
        if "left_amount_3" in mapping:
            display.append("left_c")
        if kind == "balance_sheet" and "right_item" in mapping:
            for column, field, fallback in (("right_item", "right_item", "右侧项目"), ("right_a", "right_amount_1", "右侧金额 1"), ("right_b", "right_amount_2", "右侧金额 2"), ("right_c", "right_amount_3", "右侧金额 3")):
                view.heading(column, text=label(field, fallback))
            display.extend(("right_item", "right_a"))
            if "right_amount_2" in mapping:
                display.append("right_b")
            if "right_amount_3" in mapping:
                display.append("right_c")
        display.append("source")
        view.configure(displaycolumns=display)
        self.report_info[kind].set(f'来源：{source["original_path"]}  ·  工作表：{source["sheet"]}  ·  共 {source["row_count"]} 行；空白金额留空，零值显示 0.00')
        for row in self.store.report_rows(source_id):
            values = [row["left_item"], report_amount(row["left_amount_1_cents"]), report_amount(row["left_amount_2_cents"]), report_amount(row["left_amount_3_cents"])]
            if kind == "balance_sheet":
                values.extend((row["right_item"], report_amount(row["right_amount_1_cents"]), report_amount(row["right_amount_2_cents"]), report_amount(row["right_amount_3_cents"])))
            values.append(f'{source["filename"]}/{source["sheet"]}:{row["row_no"]}')
            view.insert("", "end", values=values)

    def refresh_details(self) -> None:
        self.detail_view.delete(*self.detail_view.get_children())
        if not self.store:
            return
        year, period = self._selected_period()
        rows_found = self.store.entries(account=self.account, search=self.search.get().strip(), fiscal_year=year, period=period)
        marked_keys = {row["voucher_key"] for row in self.store.db.execute("SELECT voucher_key FROM voucher_marks")}
        parties = self.store.related_parties()
        for row in rows_found:
            side_tag = "debit_row" if row["debit_cents"] and not row["credit_cents"] else "credit_row" if row["credit_cents"] and not row["debit_cents"] else ""
            self.detail_view.insert("", "end", iid=str(row["id"]), values=("★" if voucher_key(row) in marked_keys else "", row["entry_date"], row["fiscal_year"], row["period"], f'{row["voucher_type"]}{row["voucher_no"]}', row["code"], row["name"], row["summary"], row["counterparty"], related_party_match(row, parties), row["auxiliary"], row["currency"], row["original_amount"], amount(row["debit_cents"]), amount(row["credit_cents"]), row["maker"], f'{row["filename"]}/{row["sheet"]}:{row["row_no"]}'), tags=(side_tag,) if side_tag else ())
        if len(rows_found) == 3000:
            self.status.set("明细仅显示前 3000 行；请按科目或关键词缩小范围。")

    def open_account(self, _event: object) -> None:
        selection = self.summary_view.selection()
        if not selection:
            return
        self.account = selection[0]
        values = self.summary_view.item(selection[0], "values")
        self.account_label.set(f"{values[0]} {values[1]}".strip())
        self.refresh_details()
        self._refresh_aux()
        self.tabs.select(1)

    def open_voucher(self, _event: object) -> None:
        if not self.store:
            return
        selection = self.detail_view.selection()
        if not selection:
            return
        lines = self.store.voucher_lines(int(selection[0]))
        if not lines:
            return
        if self.store.setting("voucher_open_mode", "replace") != "multiple":
            for old in list(self.voucher_windows):
                if old.winfo_exists():
                    old.destroy()
            self.voucher_windows.clear()
        target = lines[0]
        dialog = tk.Toplevel(self)
        self.voucher_windows.add(dialog)
        dialog.bind("<Destroy>", lambda event: self.voucher_windows.discard(dialog) if event.widget is dialog else None)
        dialog.title(f'凭证分录 · {target["fiscal_year"]}年{target["period"]}期 {target["voucher_type"]}{target["voucher_no"]}')
        dialog.geometry(f"760x{min(430, max(240, 185 + len(lines) * 28))}")
        voucher_top = ttk.Frame(dialog, padding=10)
        voucher_top.pack(fill="x")
        ttk.Label(voucher_top, text="根据已导入明细重组的凭证分录；不是原始凭证扫描件。", foreground="#995300").pack(side="left")
        ttk.Button(voucher_top, text="还原默认格式", command=self.reset_voucher_format).pack(side="right")
        ttk.Button(voucher_top, text="显示／隐藏列", command=lambda: self._open_columns(dialog, "voucher")).pack(side="right", padx=6)
        ttk.Button(voucher_top, text="关注／注释", command=lambda: NoteDialog(self, entry_id=int(selection[0]))).pack(side="right", padx=6)
        view = tree(dialog, VIEW_COLUMNS["voucher"])
        view.configure(height=min(9, max(2, len(lines))))
        self._register_column_view("voucher", view)
        for key, width in self.store.voucher_widths().items():
            if key in view["columns"]:
                view.column(key, width=width)
        view.bind("<ButtonRelease-1>", lambda _e, widget=view: self._save_voucher_widths(widget))
        for line in lines:
            side_tag = "debit_row" if line["debit_cents"] and not line["credit_cents"] else "credit_row" if line["credit_cents"] and not line["debit_cents"] else ""
            view.insert("", "end", values=(line["code"], line["name"], line["summary"], line["auxiliary"], line["currency"], line["original_amount"], amount(line["debit_cents"]), amount(line["credit_cents"]), f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'), tags=(side_tag,) if side_tag else ())
        debit = sum(line["debit_cents"] for line in lines)
        credit = sum(line["credit_cents"] for line in lines)
        ttk.Label(dialog, text=f"已导入分录：{len(lines)} 行   借方 {amount(debit)}   贷方 {amount(credit)}   差额 {amount(debit-credit) or '0.00'}", padding=12).pack(anchor="w")

    def _save_voucher_widths(self, view: ttk.Treeview) -> None:
        if self.store and view.winfo_exists():
            self.store.save_voucher_widths({key: int(view.column(key, "width")) for key, _, _ in VIEW_COLUMNS["voucher"]})

    def reset_voucher_format(self) -> None:
        if not self.store:
            return
        self.store.save_display_columns("voucher", DEFAULT_COLUMNS["voucher"])
        self.store.reset_voucher_widths()
        self.column_views["voucher"] = [view for view in self.column_views["voucher"] if view.winfo_exists()]
        for view in self.column_views["voucher"]:
            view.configure(displaycolumns=DEFAULT_COLUMNS["voucher"])
            for key, _, width in VIEW_COLUMNS["voucher"]:
                view.column(key, width=width)


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self._configure_theme()
        self.preferences_path = Path.home() / ".dudledger" / "preferences.json"
        default_size = abs(tkfont.nametofont("TkDefaultFont").cget("size"))
        try:
            saved_size = json.loads(self.preferences_path.read_text(encoding="utf-8")).get("font_size", default_size)
        except (OSError, ValueError, AttributeError):
            saved_size = default_size
        if type(saved_size) is not int or not 9 <= saved_size <= 20:
            saved_size = default_size
        self.font_size = default_size
        self.set_font_size(saved_size, persist=False)
        self.withdraw()
        self.windows: set[App] = set()
        self.book_windows: dict[Path, App] = {}
        self.new_window()

    def _configure_theme(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        canvas = "#F5F8F7"
        ink = "#263A43"
        style.configure("TFrame", background=canvas)
        style.configure("TLabel", background=canvas, foreground=ink)
        style.configure("TLabelframe", background=canvas)
        style.configure("TLabelframe.Label", background=canvas, foreground=ink)
        style.configure("TButton", padding=(9, 6))
        style.configure("TNotebook", background=canvas, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(11, 7))
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=ink, borderwidth=1, relief="solid")
        style.configure("Treeview.Heading", background="#E9F0F0", foreground=ink, padding=(8, 6), relief="flat")
        style.map("Treeview", background=[("selected", "#DCEBF0")], foreground=[("selected", ink)])
        style.map("Treeview.Heading", background=[("active", "#DDE9E9")])
        self.intro_font = tkfont.Font(root=self, size=abs(tkfont.nametofont("TkDefaultFont").cget("size")) + 3, weight="bold")
        style.configure("Intro.TLabel", background=canvas, foreground="#28536A", font=self.intro_font)
        style.configure("Hint.TLabel", background=canvas, foreground="#59707B")

    def set_font_size(self, size: int, persist: bool = True) -> None:
        if type(size) is not int or not 9 <= size <= 20:
            raise ValueError("字体大小须在 9—20 之间")
        for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        ttk.Style(self).configure("Treeview", rowheight=max(22, size + 14))
        self.intro_font.configure(size=size + 3)
        self.font_size = size
        for window in getattr(self, "windows", ()):
            pending = [window]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                grid = getattr(widget, "dashed_grid", None)
                if grid is not None:
                    grid.schedule()
        if persist:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.preferences_path.parent, prefix=".preferences.", delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump({"font_size": size}, handle)
                os.replace(temporary, self.preferences_path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()

    def new_window(self) -> App:
        window = App(self)
        offset = 80 + 40 * (len(self.windows) % 6)
        window.geometry(f"+{offset}+{offset}")
        self.windows.add(window)
        return window

    def open_book(self, path: Path, current: App) -> None:
        canonical = path.resolve()
        existing = self.book_windows.get(canonical)
        if existing and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        target = current if current.store is None else self.new_window()
        target._use_book(path)
        if target.store:
            self.book_windows[canonical] = target
        elif target is not current:
            target._close()


def main() -> None:
    Application().mainloop()


if __name__ == "__main__":
    main()
