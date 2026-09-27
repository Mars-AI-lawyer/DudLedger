"""Tk desktop interface; Jev networking requires an explicit preview confirmation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .checks import run_checks
from .deepseek_mapping import delete_api_key as deepseek_delete_api_key, get_api_key as deepseek_get_api_key, key_memory_only as deepseek_memory_only, save_api_key as deepseek_save_api_key, suggest_mapping as deepseek_suggest_mapping
from .grid_lines import DashedGrid
from .importer import REPORT_KINDS, ImportSpec, classify_sheet, is_cashflow_sheet, file_hash, format_money, guess_header, guess_mapping, header_for_row, rows, sheet_names, text_value
from .jev_mapping import PREVIEW_COLUMNS, delete_api_key, get_api_key, key_memory_only, preview_sheet, save_api_key, suggest_mapping
from .precheck import SEVERITY_CRITICAL, SEVERITY_WARNING, precheck
from .reconciliation import empty_plan, load_plan, save_plan
from .related_parties import PARTY_ROLES, parse_party_lines, party_entries_from_file, related_party_match
from .store import LedgerStore
from .theme import configure_theme
from .voucher_notes import export_marks_md, list_marks, mark_for_entry, remove_mark, save_mark, save_mark_by_key, source_account, update_mark_note, voucher_key


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

PROVIDER_LABELS = {"jev": "Jev", "deepseek": "DeepSeek"}


def ai_tools(controller) -> tuple[str, object, object]:
    """Return (label, suggest, get_key) for the configured column-suggestion provider."""
    if getattr(controller, "ai_provider", "jev") == "deepseek":
        return "DeepSeek", deepseek_suggest_mapping, deepseek_get_api_key
    return "Jev", suggest_mapping, get_api_key

VIEW_COLUMNS = {
    "balance": [("code", "科目编码", 125), ("name", "科目名称", 200), ("od", "期初借方", 115), ("oc", "期初贷方", 115), ("pd", "本期借方", 115), ("pc", "本期贷方", 115), ("yd", "本年累计借方", 125), ("yc", "本年累计贷方", 125), ("cd", "期末借方", 115), ("cc", "期末贷方", 115), ("n", "明细行数", 80)],
    "detail": [("marked", "关注", 55), ("date", "日期", 105), ("year", "年度", 70), ("period", "期间", 65), ("voucher", "凭证", 120), ("code", "科目编码", 110), ("name", "科目名称", 180), ("summary", "摘要", 250), ("counterparty", "往来单位", 145), ("related", "用户标注关联方", 145), ("aux", "核算维度", 145), ("currency", "币别", 80), ("original", "原币金额", 100), ("debit", "借方", 105), ("credit", "贷方", 105), ("maker", "制单人", 90), ("source", "来源行", 200)],
    "voucher": [("code", "科目编码", 90), ("name", "科目名称", 125), ("summary", "摘要", 190), ("aux", "核算维度", 140), ("currency", "币别", 75), ("original", "原币金额", 100), ("debit", "借方", 90), ("credit", "贷方", 90), ("source", "来源行", 135)],
}

DEFAULT_COLUMNS = {
    "balance": ["code", "name", "pd", "pc", "cd", "cc", "n"],
    "detail": ["marked", "date", "voucher", "name", "summary", "counterparty", "debit", "credit"],
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
        view.dashed_grid.schedule_scroll()
    def on_x_scroll(first: str, last: str) -> None:
        xbar.set(first, last)
        view.dashed_grid.schedule_scroll()
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
    view.tag_configure("marked", background="#FFF1F1")
    view.tag_configure("account_parent", foreground="#514F46")
    def on_wheel(event: tk.Event) -> str | None:
        owner = parent.winfo_toplevel()
        controller = getattr(owner, "controller", None) or getattr(getattr(owner, "ledger_app", None), "controller", None)
        mode = getattr(controller, "scroll_mode", "system")
        if mode == "system" or not event.delta:
            return None
        view.yview_scroll((-1 if event.delta > 0 else 1) * (1 if mode == "gentle" else 5), "units")
        return "break"
    view.bind("<MouseWheel>", on_wheel, add="+")
    owner = parent.winfo_toplevel()
    controller = getattr(owner, "controller", None) or getattr(getattr(owner, "ledger_app", None), "controller", None)
    if controller is not None:
        view.dashed_grid.set_enabled(controller.show_grid)
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
        self.protocol("WM_DELETE_WINDOW", self._finish)
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="凭证打开方式").pack(anchor="w")
        current = parent.store.setting("voucher_open_mode", "replace") if parent.store else "replace"
        self.mode = tk.StringVar(value=current if current in ("replace", "multiple") else "replace")
        for label, value in (("新凭证自动关闭前一张", "replace"), ("连续打开，保留已打开凭证", "multiple")):
            ttk.Radiobutton(body, text=label, variable=self.mode, value=value, command=self._save_mode, state="normal" if parent.store else "disabled").pack(anchor="w", pady=3)
        home_row = ttk.Frame(body)
        home_row.pack(fill="x", pady=(9, 0))
        ttk.Label(home_row, text="打开账套后显示").pack(side="left")
        self.home_view = tk.StringVar(value="明细账" if parent.store and parent.store.setting("home_view", "summary") == "detail" else "科目总览")
        home_combo = ttk.Combobox(home_row, textvariable=self.home_view, values=("科目总览", "明细账"), state="readonly" if parent.store else "disabled", width=11)
        home_combo.pack(side="left", padx=8)
        home_combo.bind("<<ComboboxSelected>>", lambda _event: parent.store.save_setting("home_view", "detail" if self.home_view.get() == "明细账" else "summary"))
        ttk.Separator(body).pack(fill="x", pady=12)
        font_row = ttk.Frame(body)
        font_row.pack(fill="x")
        ttk.Label(font_row, text="字体大小").pack(side="left")
        self.font_size = tk.StringVar(value=str(parent.controller.font_size))
        font_combo = ttk.Combobox(font_row, textvariable=self.font_size, values=tuple(str(size) for size in range(9, 21)), state="readonly", width=5)
        font_combo.pack(side="left", padx=8)
        font_combo.bind("<<ComboboxSelected>>", lambda _event: parent.controller.set_font_size(int(self.font_size.get())))
        ttk.Separator(body).pack(fill="x", pady=12)
        ttk.Label(body, text="浏览体验").pack(anchor="w")
        scroll_row = ttk.Frame(body)
        scroll_row.pack(fill="x", pady=(6, 0))
        ttk.Label(scroll_row, text="滚动速度").pack(side="left")
        self.scroll_mode = tk.StringVar(value={"system": "跟随系统", "gentle": "舒缓", "quick": "快速"}.get(parent.controller.scroll_mode, "跟随系统"))
        scroll_combo = ttk.Combobox(scroll_row, textvariable=self.scroll_mode, values=("跟随系统", "舒缓", "快速"), state="readonly", width=10)
        scroll_combo.pack(side="left", padx=8)
        scroll_combo.bind("<<ComboboxSelected>>", lambda _event: parent.controller.set_scroll_mode({"跟随系统": "system", "舒缓": "gentle", "快速": "quick"}[self.scroll_mode.get()]))
        self.grid_lines = tk.BooleanVar(value=parent.controller.show_grid)
        ttk.Checkbutton(body, text="显示表格虚线（关闭后大表滚动更轻快）", variable=self.grid_lines,
                        command=lambda: parent.controller.set_grid_lines(self.grid_lines.get())).pack(anchor="w", pady=(7, 0))
        ttk.Separator(body).pack(fill="x", pady=12)
        ttk.Label(body, text="AI 建议列模型（导入时建议表头与列，可选）").pack(anchor="w")
        provider_row = ttk.Frame(body)
        provider_row.pack(fill="x", pady=(5, 0))
        self.provider = tk.StringVar(value=PROVIDER_LABELS.get(parent.controller.ai_provider, "Jev"))
        ttk.Radiobutton(provider_row, text="Jev（TypeSafe）", variable=self.provider, value="Jev", command=self._save_provider).pack(side="left")
        ttk.Radiobutton(provider_row, text="DeepSeek（deepseek-flash，不思考）", variable=self.provider, value="DeepSeek", command=self._save_provider).pack(side="left", padx=12)
        ttk.Label(body, text="Jev API Key").pack(anchor="w", pady=(7, 0))
        key_row = ttk.Frame(body)
        key_row.pack(fill="x", pady=(5, 0))
        try:
            self.api_key = tk.StringVar(value=get_api_key())
        except Exception:
            self.api_key = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.api_key, show="•", width=29)
        self.key_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(key_row, text="保存密钥", command=self._save_api_key).pack(side="left", padx=5)
        ttk.Button(key_row, text="删除密钥", command=self._delete_api_key).pack(side="left")
        self.key_status = tk.StringVar()
        ttk.Label(body, textvariable=self.key_status, foreground="#555").pack(anchor="w", pady=(4, 0))
        self._refresh_key_status()
        ttk.Label(body, text="DeepSeek API Key").pack(anchor="w", pady=(7, 0))
        deepseek_row = ttk.Frame(body)
        deepseek_row.pack(fill="x", pady=(5, 0))
        try:
            self.deepseek_key = tk.StringVar(value=deepseek_get_api_key())
        except Exception:
            self.deepseek_key = tk.StringVar()
        self.deepseek_entry = ttk.Entry(deepseek_row, textvariable=self.deepseek_key, show="•", width=29)
        self.deepseek_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(deepseek_row, text="保存密钥", command=self._save_deepseek_key).pack(side="left", padx=5)
        ttk.Button(deepseek_row, text="删除密钥", command=self._delete_deepseek_key).pack(side="left")
        self.deepseek_status = tk.StringVar()
        ttk.Label(body, textvariable=self.deepseek_status, foreground="#555").pack(anchor="w", pady=(4, 0))
        self._refresh_deepseek_status()
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="显示明文（两个密钥）", variable=self.show_key, command=self._toggle_key_visible).pack(anchor="w", pady=(2, 0))
        jev_actions = ttk.Frame(body)
        jev_actions.pack(fill="x", pady=6)
        from .jev_ui import launch_connection_test
        ttk.Button(jev_actions, text="测试连接（虚构内容）", command=lambda: launch_connection_test(self, self.key_status)).pack(side="left")
        ttk.Button(jev_actions, text="Jev 审查规则与尽调筛选设置", command=parent.open_review_rules, state="normal" if parent.store else "disabled").pack(side="left", padx=6)
        ttk.Label(body, text="批量导入默认勾选 AI 建议列；确认发送范围后才联网，也可取消勾选。", foreground="#555").pack(anchor="w", pady=(7, 0))
        ttk.Label(body, text="字体、滚动和表格线适用于所有窗口；凭证与首页偏好只适用于当前账套。", foreground="#555").pack(anchor="w", pady=(14, 0))
        ttk.Button(body, text="保存并完成", command=self._finish).pack(anchor="e", pady=(12, 0))

    def _save_mode(self) -> None:
        if self.parent.store:
            self.parent.store.save_setting("voucher_open_mode", self.mode.get())

    def _save_provider(self) -> None:
        provider = next((code for code, label in PROVIDER_LABELS.items() if label == self.provider.get()), "jev")
        self.parent.controller.set_ai_provider(provider)

    def _refresh_key_status(self) -> None:
        try:
            present = bool(get_api_key())
            self.key_status.set(("密钥仅保留在本次运行内存，重启后需重新输入" if key_memory_only() else "密钥已保存到系统安全凭据库（默认以 • 隐藏，可勾选“显示明文”查看）") if present else "尚未保存密钥")
        except Exception:
            self.key_status.set("无法访问系统凭据库")

    def _refresh_deepseek_status(self) -> None:
        try:
            present = bool(deepseek_get_api_key())
            self.deepseek_status.set(("密钥仅保留在本次运行内存，重启后需重新输入" if deepseek_memory_only() else "密钥已保存到系统安全凭据库（默认以 • 隐藏，可勾选“显示明文”查看）") if present else "尚未保存密钥")
        except Exception:
            self.deepseek_status.set("无法访问系统凭据库")

    def _toggle_key_visible(self) -> None:
        show = "" if self.show_key.get() else "•"
        self.key_entry.configure(show=show)
        self.deepseek_entry.configure(show=show)

    def _save_api_key(self) -> bool:
        try:
            save_api_key(self.api_key.get())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False
        self.api_key.set(get_api_key())
        self._refresh_key_status()
        return True

    def _save_deepseek_key(self) -> bool:
        try:
            deepseek_save_api_key(self.deepseek_key.get())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return False
        self.deepseek_key.set(deepseek_get_api_key())
        self._refresh_deepseek_status()
        return True

    def _finish(self) -> None:
        if self.api_key.get() and self.api_key.get() != get_api_key() and not self._save_api_key():
            return
        if self.deepseek_key.get() and self.deepseek_key.get() != deepseek_get_api_key() and not self._save_deepseek_key():
            return
        self.destroy()

    def _delete_api_key(self) -> None:
        try:
            delete_api_key()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.api_key.set("")
        self._refresh_key_status()

    def _delete_deepseek_key(self) -> None:
        try:
            deepseek_delete_api_key()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.deepseek_key.set("")
        self._refresh_deepseek_status()


class RelatedPartyImportDialog(tk.Toplevel):
    """Paste names or load a spreadsheet; each first non-empty cell becomes one party."""

    def __init__(self, parent: "RelatedPartiesDialog"):
        super().__init__(parent)
        self.dialog = parent
        self.title("批量导入尽调主体")
        self.geometry("600x500")
        self.minsize(500, 400)
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, foreground="#555", wraplength=540, text="每行一个主体，可用“｜”补充身份，例如“张三｜股东,董事”；空白行与重复项自动忽略。"
            "也可从 Excel/CSV/TXT 导入：第一列名称，第二列身份（如“股东、董事”，识别不了的自动留空），首行表头自动跳过。"
            "与现有名单重复或无效的条目导入时跳过。").pack(anchor="w")
        ttk.Label(body, foreground="#555", wraplength=540, text=f"可选身份：{'、'.join(PARTY_ROLES)}").pack(anchor="w", pady=(4, 0))
        self.editor = tk.Text(body, height=11, wrap="word", undo=True)
        self.editor.pack(fill="both", expand=True, pady=(8, 6))
        self.status = tk.StringVar()
        ttk.Label(body, textvariable=self.status, foreground="#555").pack(anchor="w")
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="从文件导入…", command=self._load_file).pack(side="left")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="导入名单", command=self._import).pack(side="right", padx=6)
        self.editor.bind("<KeyRelease>", lambda _e: self._refresh_count())
        self.grab_set()

    def _entries(self) -> list[tuple[str, list[str]]]:
        return parse_party_lines(self.editor.get("1.0", "end-1c"))

    def _refresh_count(self) -> None:
        self.status.set(f"当前识别到 {len(self._entries())} 个主体")

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="选择尽调主体清单文件",
            filetypes=[("表格与文本", "*.xlsx *.xlsm *.xls *.csv *.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            entries = party_entries_from_file(path)
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)
            return
        if not entries:
            messagebox.showinfo("没有可导入的主体", "文件中没有读到非空内容。", parent=self)
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", "\n".join(name + (f"｜{','.join(roles)}" if roles else "") for name, roles in entries))
        self._refresh_count()
        self.status.set(f"已从文件读取 {len(entries)} 个主体，可先修改再导入")

    def _import(self) -> None:
        entries = self._entries()
        if not entries:
            messagebox.showinfo("没有可导入的主体", "请先粘贴名称（每行一个，可选“｜身份”）或从文件读取。", parent=self)
            return
        added, skipped = 0, 0
        for name, roles in entries:
            try:
                self.dialog.parent.store.save_related_party(name, [], "", None, roles, "")
                added += 1
            except ValueError:
                skipped += 1
        self.dialog._refresh()
        messagebox.showinfo("导入完成", f"新增 {added} 条，跳过重复或无效 {skipped} 条。", parent=self)
        self.destroy()


class RelatedPartiesDialog(tk.Toplevel):
    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.title("当前项目 · 尽调主体名单")
        self.geometry("760x580")
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="名单由用户指定；本地对往来单位、辅助核算作精确匹配，命中作为程序特征随凭证发送。AI 审查时名称、别名与身份（不含备注、关系）会随每张凭证发送到 Jev 云端，用于识别关联资金往来，不构成法律关系认定。", foreground="#555", wraplength=700).pack(anchor="w")
        self.view = ttk.Treeview(body, columns=("name", "roles", "relation", "aliases", "note"), show="headings", height=8)
        for key, title, width in (("name", "名称", 150), ("roles", "身份", 130), ("relation", "与目标公司关系", 130), ("aliases", "别名", 140), ("note", "备注", 150)):
            self.view.heading(key, text=title)
            self.view.column(key, width=width)
        self.view.pack(fill="both", expand=True, pady=(8, 10))
        self.view.bind("<<TreeviewSelect>>", self._select)
        form = ttk.Frame(body)
        form.pack(fill="x")
        ttk.Label(form, text="名称").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="身份（逗号分隔）").grid(row=1, column=0, sticky="w")
        self.roles_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.roles_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="与目标公司关系").grid(row=2, column=0, sticky="w")
        self.relation_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.relation_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="别名（每行一个）").grid(row=3, column=0, sticky="nw")
        self.alias_editor = tk.Text(form, height=3, wrap="word")
        self.alias_editor.grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="备注／依据").grid(row=4, column=0, sticky="nw")
        self.note_editor = tk.Text(form, height=3, wrap="word")
        self.note_editor.grid(row=4, column=1, sticky="ew", pady=3)
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="新增", command=self._clear).pack(side="left")
        ttk.Button(buttons, text="批量导入", command=lambda: RelatedPartyImportDialog(self)).pack(side="left", padx=6)
        ttk.Button(buttons, text="删除所选", command=self._delete).pack(side="left")
        ttk.Button(buttons, text="完成", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=6)
        self._refresh()
        self.grab_set()

    def _refresh(self) -> None:
        self.view.delete(*self.view.get_children())
        for row in self.parent.store.related_parties():
            roles = "、".join(json.loads(row["roles_json"])) if "roles_json" in row.keys() else ""
            self.view.insert("", "end", iid=str(row["id"]), values=(row["name"], roles, row["relation"].replace("\n", " ")[:60] if "relation" in row.keys() else "",
                "、".join(json.loads(row["aliases_json"])), row["note"].replace("\n", " ")[:80]))
        self.parent.refresh_details()

    def _select(self, _event: object) -> None:
        selected = self.view.selection()
        if not selected:
            return
        row = next((item for item in self.parent.store.related_parties() if item["id"] == int(selected[0])), None)
        if row is None:
            return
        self.name_var.set(row["name"])
        self.roles_var.set("、".join(json.loads(row["roles_json"])) if "roles_json" in row.keys() else "")
        self.relation_var.set(row["relation"] if "relation" in row.keys() else "")
        self.alias_editor.delete("1.0", "end")
        self.alias_editor.insert("1.0", "\n".join(json.loads(row["aliases_json"])))
        self.note_editor.delete("1.0", "end")
        self.note_editor.insert("1.0", row["note"])

    def _clear(self) -> None:
        selected = self.view.selection()
        if selected:
            self.view.selection_remove(*selected)
        self.name_var.set("")
        self.roles_var.set("")
        self.relation_var.set("")
        self.alias_editor.delete("1.0", "end")
        self.note_editor.delete("1.0", "end")

    def _save(self) -> None:
        selected = self.view.selection()
        party_id = int(selected[0]) if selected else None
        try:
            saved_id = self.parent.store.save_related_party(self.name_var.get(), self.alias_editor.get("1.0", "end-1c").splitlines(),
                self.note_editor.get("1.0", "end-1c"), party_id, self.roles_var.get(), self.relation_var.get())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self._refresh()
        self.view.selection_set(str(saved_id))

    def _delete(self) -> None:
        selected = self.view.selection()
        if selected and messagebox.askyesno("删除尽调主体", "确定从当前项目删除这条用户维护的尽调主体记录吗？", parent=self):
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


class PrecheckDialog(tk.Toplevel):
    """集中确认疑点：原单元格 → 当前解释 → 问题原因 → 可选处理。

    存在未解决的关键解析错误时该工作表暂停（不导入、不偷偷跳过坏行）；
    其他通过检查的工作表可继续。排除所选行后立即重新预检。
    """

    LEVEL_LABELS = {SEVERITY_CRITICAL: "关键", SEVERITY_WARNING: "提醒", "info": "说明", "excluded": "排除"}

    def __init__(self, parent, work: list, on_done, allow_back: bool = False):
        super().__init__(parent)
        self.parent_widget = parent
        self.work = [[spec, result] for spec, result in work]
        self.on_done = on_done
        self.allow_back = allow_back
        self.title("导入预检 · 集中确认疑点")
        self.geometry("980x600")
        self.minsize(720, 440)
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="每一行都有去向：导入、排除（含原因）或待处理。关键问题解决前该表暂停导入；排除某行只是不导入它，不会改动原表。", wraplength=920, foreground="#555").pack(anchor="w")
        self.summary = tk.StringVar()
        ttk.Label(body, textvariable=self.summary, foreground="#31435B").pack(anchor="w", pady=(6, 4))
        self.view = tree(body, [("level", "级别", 55), ("row", "行", 55), ("raw", "原单元格", 260), ("interp", "当前解释", 130), ("reason", "问题原因", 260), ("action", "可选处理", 200)])
        self.view.configure(selectmode="extended")
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="排除所选行并重新预检", command=self._exclude_selected).pack(side="left")
        self.hint = ttk.Label(footer, text="选中“关键/提醒”行后可排除该行；排除后自动重新预检。", foreground="#555")
        self.hint.pack(side="left", padx=10)
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=4)
        self.proceed_button = ttk.Button(footer, text="继续导入", command=self._proceed)
        self.proceed_button.pack(side="right", padx=4)
        if allow_back:
            ttk.Button(footer, text="返回调整列设置", command=self.destroy).pack(side="right", padx=4)
        self.grab_set()
        self._refresh()

    def _refresh(self) -> None:
        self.view.delete(*self.view.get_children())
        blocked = 0
        for index, (spec, result) in enumerate(self.work):
            node = self.view.insert("", "end", iid=f"sheet{index}", open=True,
                values=("表", str(spec.header_row), f"{spec.path.name} / {spec.sheet}", "", result.summary_line(), ""))
            if result is None:
                self.view.insert(node, "end", values=(self.LEVEL_LABELS[SEVERITY_CRITICAL], "", spec.path.name, "预检失败", "整表无法读取或解析", "该表暂停；请修正后重试"))
                blocked += 1
                continue
            if result.blocked:
                blocked += 1
                self.view.item(node, values=("表", str(spec.header_row), f"{spec.path.name} / {spec.sheet}", "暂停", result.summary_line() + "（存在未解决的关键问题）", ""))
            for issue in result.issues:
                self.view.insert(node, "end", values=(self.LEVEL_LABELS.get(issue.severity, issue.severity), str(issue.row_no or ""), issue.raw or issue.column, issue.interpretation, issue.reason, issue.action))
            shown = result.excluded_samples[:60]
            for decision in shown:
                self.view.insert(node, "end", values=(self.LEVEL_LABELS["excluded"], str(decision.row_no), "", "不导入", decision.reason, ""))
            hidden = len(result.excluded_samples) - len(shown)
            if hidden > 0 or result.stats.excluded_total > len(shown):
                self.view.insert(node, "end", values=(self.LEVEL_LABELS["excluded"], "", "", "不导入", f"其余排除行共 {result.stats.excluded_total} 行（详见导入质量报告）", ""))
        importable = [item for item in self.work if item[1] is not None and not item[1].blocked]
        self.summary.set(f"共 {len(self.work)} 张表：可导入 {len(importable)} 张，暂停 {blocked} 张。")
        self.proceed_button.configure(text=f"继续导入（{len(importable)} 张）", state="normal" if importable else "disabled")

    def _exclude_selected(self) -> None:
        by_sheet: dict[int, set[int]] = {}
        for item in self.view.selection():
            if str(item).startswith("sheet"):
                continue
            index = int(self.view.parent(item).replace("sheet", ""))
            values = self.view.item(item, "values")
            if values and str(values[1]).isdigit():
                by_sheet.setdefault(index, set()).add(int(values[1]))
        if not by_sheet:
            messagebox.showinfo("未选择可排除的行", "请先选中带行号的“关键/提醒”行。", parent=self)
            return
        for index, row_nos in by_sheet.items():
            spec = self.work[index][0]
            spec = spec.with_extra_exclusions(tuple(row_nos))
            try:
                self.work[index] = [spec, precheck(spec)]
            except Exception as exc:
                messagebox.showerror("重新预检失败", str(exc), parent=self)
                return
        self._refresh()

    def _proceed(self) -> None:
        proceed = [(spec, result) for spec, result in self.work if result is not None and not result.blocked]
        if not proceed:
            return
        dropped = len(self.work) - len(proceed)
        self.destroy()
        self.on_done(proceed, dropped)


class ImportDialog(tk.Toplevel):
    def __init__(self, parent: "App", path: Path, *, spec=None, sheet_name=None, on_save=None):
        super().__init__(parent)
        self.parent = parent
        self.path = path
        self.on_save = on_save
        self.title("导入资料 · 核对每列含义")
        width = min(960, max(600, self.winfo_screenwidth() - 80))
        height = min(780, max(500, self.winfo_screenheight() - 150))
        self.geometry(f"{width}x{height}")
        self.transient(parent)
        self.grab_set()
        self.sheet = tk.StringVar(value=sheet_name or sheet_names(path)[0])
        self.kind = tk.StringVar(value="明细账／序时账")
        self.header = tk.StringVar(value="1")
        self.columns: list[str] = []
        self.vars: dict[str, tk.StringVar] = {}
        self._ai_queue: Queue = Queue()
        self._ai_sheet = ""
        self._layout()
        if spec is not None:
            self.kind.set(BatchImportDialog.LABELS[spec.kind])
            self.header.set(str(spec.header_row))
            self.refresh_preview(manual=True)
            for field, variable in self.vars.items():
                variable.set(self.columns[spec.mapping[field]] if field in spec.mapping else "不导入")
        else:
            self._auto_kind_for_sheet()
            self.refresh_preview(manual=on_save is not None)

    def _auto_kind_for_sheet(self) -> None:
        templates = self.parent.store.template_candidates() if self.parent.store else None
        spec = classify_sheet(self.path, self.sheet.get(), templates)
        if spec is not None:
            self.kind.set({"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}[spec.kind])

    def _layout(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text=self.path.name, font=("", 14, "bold")).pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="工作表").pack(side="left")
        sheet = ttk.Combobox(row, textvariable=self.sheet, values=sheet_names(self.path), state="disabled" if self.on_save else "readonly", width=25)
        sheet.pack(side="left", padx=(6, 18))
        sheet.bind("<<ComboboxSelected>>", lambda _e: (self._auto_kind_for_sheet(), self.refresh_preview()))
        ttk.Label(row, text="表类型").pack(side="left")
        kind = ttk.Combobox(row, textvariable=self.kind, values=("明细账／序时账", "科目余额表", "资产负债表", "利润表"), state="readonly", width=16)
        kind.pack(side="left", padx=(6, 18))
        kind.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        header_actions = ttk.Frame(top)
        header_actions.pack(fill="x", pady=(8, 0))
        ttk.Label(header_actions, text="表头行号").pack(side="left")
        ttk.Entry(header_actions, textvariable=self.header, width=5).pack(side="left", padx=6)
        ttk.Button(header_actions, text="按行号读取表头", command=lambda: self.refresh_preview(manual=True)).pack(side="left", padx=5)
        self.ai_button = ttk.Button(header_actions, text="AI 建议列（可选）", command=self._offer_ai)
        self.ai_button.pack(side="right")
        ttk.Label(top, text="先看原表，再确认每个字段对应的列。〔空〕与原表的 0 分开显示。", foreground="#555").pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(self, height=5, wrap="none", state="disabled", font="TkFixedFont")
        self.preview.pack(fill="x", padx=12, pady=8)
        bottom = ttk.Frame(self, padding=12)
        bottom.pack(side="bottom", fill="x")
        self.import_note = ttk.Label(bottom, text="账簿空白/小计行会跳过；请确认借贷列不是余额列。", foreground="#995300")
        self.import_note.pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="保存列设置" if self.on_save else "确认并导入", command=self.import_now).pack(side="right", padx=4)
        map_holder = ttk.Frame(self)
        map_holder.pack(fill="both", expand=True, padx=12, pady=4)
        self.map_canvas = tk.Canvas(map_holder, highlightthickness=0, background="#F7F5F1")
        map_scroll = ttk.Scrollbar(map_holder, orient="vertical", command=self.map_canvas.yview)
        self.map_canvas.configure(yscrollcommand=map_scroll.set)
        self.map_canvas.pack(side="left", fill="both", expand=True)
        map_scroll.pack(side="right", fill="y")
        self.map_frame = ttk.LabelFrame(self.map_canvas, text="每一列的含义 · 可滚动", padding=12)
        self.map_window = self.map_canvas.create_window((0, 0), window=self.map_frame, anchor="nw")
        self.map_frame.bind("<Configure>", lambda _event: self.map_canvas.configure(scrollregion=self.map_canvas.bbox("all")))
        self.map_canvas.bind("<Configure>", lambda event: self.map_canvas.itemconfigure(self.map_window, width=event.width))

    def _offer_ai(self) -> None:
        label, suggest, get_key = ai_tools(self.parent.controller)
        try:
            api_key = get_key()
            if not api_key:
                self.grab_release()
                settings = SettingsDialog(self.parent)
                settings.lift()
                settings.grab_set()
                self.wait_window(settings)
                if not self.winfo_exists():
                    return
                self.grab_set()
                api_key = get_key()
                if not api_key:
                    return
            sheet = self.sheet.get()
            preview = preview_sheet(self.path, sheet)
        except Exception as exc:
            messagebox.showerror("无法取得建议", str(exc), parent=self)
            return
        description = (
            f"将工作表“{sheet}”的前 {len(preview['rows'])} 行发送给 {label}；"
            f"每行最多 {PREVIEW_COLUMNS} 列，每格最多 60 字。预览可能含主体名称和金额。"
            "不发送整份文件或本地路径。是否继续？"
        )
        if not messagebox.askokcancel("确认发送预览", description, parent=self):
            return
        self._ai_sheet = sheet
        self.ai_button.configure(state="disabled", text=f"{label} 正在分析…")

        def worker() -> None:
            try:
                self._ai_queue.put((True, suggest(preview, api_key)))
            except Exception as exc:
                self._ai_queue.put((False, str(exc).replace(api_key, "[密钥已隐藏]")))

        Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_ai)

    def _poll_ai(self) -> None:
        try:
            success, result = self._ai_queue.get_nowait()
        except Empty:
            self.after(120, self._poll_ai)
            return
        self.ai_button.configure(state="normal", text="AI 建议列（可选）")
        if not success:
            messagebox.showerror("AI 建议未完成", result, parent=self)
            return
        if self.sheet.get() != self._ai_sheet:
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
        self.import_note.configure(text="已填入 AI 建议；请对照原表逐列确认后再导入。")
        if result.warnings:
            caution = "\n".join(result.warnings[:8])
            messagebox.showinfo("AI 建议需要核对", f"表类型、表头行和列映射已填入下方，但有以下需要注意之处：\n{caution}", parent=self)

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
            spec.validate()
        except Exception as exc:
            messagebox.showerror("导入失败", f"没有写入本次数据。\n{exc}", parent=self)
            return
        if self.on_save:
            self.on_save(spec)
            self.destroy()
            return
        try:
            result = precheck(spec)
        except Exception as exc:
            messagebox.showerror("预检失败", f"没有写入本次数据。\n{exc}", parent=self)
            return
        if result.needs_attention:
            PrecheckDialog(self, [(spec, result)], self._import_prechecked, allow_back=True)
            return
        self._import_prechecked([(spec, result)])

    def _import_prechecked(self, work: list, paused_count: int = 0) -> None:
        if not work:
            return
        spec, result = work[0]
        try:
            if file_hash(spec.path) != result.fingerprint:
                if not messagebox.askyesno("文件已修改", "预检之后文件内容发生了变化，建议重新预检。是否重新预检后再导入？", parent=self):
                    return
                self.import_now()
                return
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
        excluded_note = ""
        if result.stats.excluded_total:
            excluded_note = f"预检排除 {result.stats.excluded_total} 行（{result.stats.exclusion_line()}）。"
        self.destroy()
        self.parent.refresh_all()
        differences = sum(item.level == "差异" for item in run_checks(self.parent.store))
        tail = f"发现 {differences} 项差异，请从顶部“查看”菜单打开“核对提醒”。" if differences else "没有发现已确认口径下的金额差异；待核事项请从顶部“查看”菜单打开“核对提醒”。"
        messagebox.showinfo("导入完成", f"成功导入 {count} 行。{excluded_note}{tail}", parent=self.parent)


class BatchImportDialog(tk.Toplevel):
    LABELS = {"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}

    def __init__(self, parent: "App", paths: list[Path]):
        super().__init__(parent)
        self.parent = parent
        self.items = []
        self.skipped = []
        self.manual = set()
        self.ai_ready = set()
        self.ai_queue = Queue()
        self.ai_cancel = Event()
        self.ai_running = False
        templates = parent.store.template_candidates() if parent.store else None
        for path in dict.fromkeys(paths):
            try:
                names = sheet_names(path)
            except Exception as exc:
                self.skipped.append(f"{path.name}：{exc}")
                continue
            for sheet in names:
                try:
                    if is_cashflow_sheet(path, sheet):
                        continue
                    spec = classify_sheet(path, sheet, templates)
                except Exception:
                    spec = None
                self.items.append((path, sheet, spec))
        if not self.items and not self.skipped:
            self.destroy()
            return
        self.title("导入表格 · 可选择一个或多个文件")
        self.geometry("820x520")
        self.minsize(650, 420)
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="勾选要导入的工作表；需要时双击修改列设置。", foreground="#555").pack(anchor="w")
        self.use_ai = tk.BooleanVar(value=True)
        self.ai_toggle = ttk.Checkbutton(body, text="使用 AI 为所有勾选的工作表建议列（默认）", variable=self.use_ai,
                                          command=self._toggle_ai)
        self.ai_toggle.pack(anchor="w", pady=(9, 0))
        self.ai_hint = ttk.Label(body, text="导入时一次确认发送范围；AI 只读取每张表的有限预览。", foreground="#555")
        self.ai_hint.pack(anchor="w", pady=(2, 5))
        self.ai_progress = ttk.Progressbar(body, mode="determinate")
        self.ai_status = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.ai_status, foreground="#555").pack(anchor="w")
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.cancel_button = ttk.Button(footer, text="取消", command=self._close)
        self.cancel_button.pack(side="right", padx=(6, 0))
        self.import_button = ttk.Button(footer, text="导入选中的工作表", command=self._import_selected)
        self.import_button.pack(side="right")
        self.edit_button = ttk.Button(footer, text="检查／调整列…", command=self._edit_current)
        self.edit_button.pack(side="left")
        self.view = tree(body, [("selected", "导入", 55), ("file", "文件", 180),
            ("sheet", "工作表", 130), ("kind", "类型", 140), ("state", "列设置", 170)])
        self.selected = set()
        for index, _ in enumerate(self.items):
            self.selected.add(index)
            self._render_item(index)
        self.view.bind("<Button-1>", self._toggle_item)
        self.view.bind("<Double-1>", self._edit_current)
        if self.items:
            self.view.selection_set('0')
        if self.skipped:
            ttk.Label(body, text="读取失败：" + "；".join(self.skipped), wraplength=730).pack(fill="x")
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self):
        self.jev_cancel.set()
        self.destroy()

    def _toggle_ai(self):
        if self.use_ai.get():
            self.ai_hint.configure(text="导入时一次确认发送范围；AI 只读取每张表的有限预览。", foreground="#555")
        else:
            self.ai_hint.configure(text="未使用 AI，自动识别可能有误；可双击工作表手动调整列。", foreground="#995300")
            messagebox.showinfo("请核对列设置", "未使用 AI 时，自动识别可能有误。导入前可双击工作表，手动调整表头、借贷方向和金额列。", parent=self)

    def _render_item(self, index):
        path, sheet, spec = self.items[index]
        values = ("☑" if index in self.selected else "☐", path.name, sheet,
            self.LABELS[spec.kind] if spec else "待确认",
            f"AI 已建议 · 表头第 {spec.header_row} 行" if index in self.ai_ready else
            (f"手动设置 · 表头第 {spec.header_row} 行" if index in self.manual else
             (f"表头第 {spec.header_row} 行" if spec else "待 AI 建议／手动设置")))
        if self.view.exists(str(index)):
            self.view.item(str(index), values=values)
        else:
            self.view.insert('', 'end', iid=str(index), values=values)

    def _toggle_item(self, event):
        if self.ai_running:
            return
        item = self.view.identify_row(event.y)
        if item and self.view.identify_column(event.x) == '#1':
            index = int(item)
            if index in self.selected:
                self.selected.remove(index)
            else:
                self.selected.add(index)
            self._render_item(index)

    def _edit_current(self, event=None):
        if self.ai_running:
            return
        if event is not None:
            item = self.view.identify_row(event.y)
            if not item or self.view.identify_column(event.x) == '#1':
                return
        else:
            selection = self.view.selection()
            item = selection[0] if selection else None
        if item is None:
            return
        index = int(item)
        path, sheet, spec = self.items[index]
        def save(updated):
            self.items[index] = (path, sheet, updated)
            self.selected.add(index)
            self.manual.add(index)
            self.ai_ready.discard(index)
            self._render_item(index)
        editor = ImportDialog(self.parent, path, spec=spec, sheet_name=sheet, on_save=save)
        editor.transient(self)
        def restore(event):
            if event.widget is editor and self.winfo_exists():
                self.grab_set()
        editor.bind('<Destroy>', restore, add='+')

    def _import_selected(self) -> None:
        if self.ai_running:
            return
        if not self.selected:
            messagebox.showinfo("未选择表", "请至少勾选一个工作表。", parent=self)
            return
        if self.use_ai.get():
            pending = [i for i in sorted(self.selected) if i not in self.manual | self.ai_ready]
            if pending:
                label, suggest, get_key = ai_tools(self.parent.controller)
                api_key = get_key()
                if not api_key:
                    self.grab_release()
                    settings = SettingsDialog(self.parent)
                    settings.lift()
                    settings.grab_set()
                    self.wait_window(settings)
                    if not self.winfo_exists():
                        return
                    self.grab_set()
                    api_key = get_key()
                    if not api_key:
                        self.ai_status.set(f"尚未设置 {label} 密钥；可取消勾选 AI 后手动核对列。")
                        return
                description = (f"将 {len(pending)} 张工作表的预览发送给 {label}，用于建议表类型、表头和列。\n"
                    f"每张表最多前 20 行、每行前 {PREVIEW_COLUMNS} 列、每格最多 60 字；可能包含名称和金额。"
                    "不发送整份文件或本地路径。是否继续？")
                if not messagebox.askokcancel("确认批量发送预览", description, parent=self):
                    return
                self._start_ai(pending, api_key, suggest, label)
                return
        self._import_ready()

    def _start_ai(self, pending, api_key, suggest, label):
        self.ai_running = True
        self.ai_cancel.clear()
        self.import_button.configure(state="disabled")
        self.edit_button.configure(state="disabled")
        self.ai_toggle.configure(state="disabled")
        self.ai_progress.configure(maximum=len(pending), value=0)
        self.ai_progress.pack(fill="x", pady=(2, 5))
        self.ai_status.set(f"{label} 正在建议列：0 / {len(pending)}")

        def worker():
            for index in pending:
                if self.ai_cancel.is_set():
                    return
                path, sheet, _ = self.items[index]
                try:
                    result = suggest(preview_sheet(path, sheet), api_key)
                    if result.kind not in self.LABELS:
                        raise ValueError("未识别表类型")
                    spec = ImportSpec(path, sheet, result.header_row, result.kind, result.mapping)
                    spec.validate()
                    headers = header_for_row(path, sheet, result.header_row, result.kind)
                    if not headers or any(column >= len(headers) or column < 0 for column in result.mapping.values()):
                        raise ValueError("建议列超出工作表范围")
                    self.ai_queue.put((index, spec, result.warnings, None))
                except Exception as exc:
                    self.ai_queue.put((index, None, (), str(exc).replace(api_key, "[密钥已隐藏]")))
            self.ai_queue.put((None, None, (), None))

        Thread(target=worker, daemon=True).start()
        self.after(120, self._poll_ai_batch, len(pending), 0, [], label)

    def _poll_ai_batch(self, total, done, problems, label="Jev"):
        if not self.winfo_exists():
            return
        try:
            while True:
                index, spec, warnings, error = self.ai_queue.get_nowait()
                if index is None:
                    self.ai_running = False
                    self.import_button.configure(state="normal")
                    self.edit_button.configure(state="normal")
                    self.ai_toggle.configure(state="normal")
                    self.ai_progress.pack_forget()
                    if problems:
                        self.ai_status.set(f"{label} 已处理 {done} 张；请检查提示后重试或手动调整。")
                        messagebox.showwarning("列建议需要核对", "\n".join(problems[:12]), parent=self)
                    else:
                        self.ai_status.set(f"{label} 已完成 {done} 张工作表的列建议。")
                        self._import_ready()
                    return
                done += 1
                if error:
                    path, sheet, _ = self.items[index]
                    problems.append(f"{path.name}/{sheet}：{error}")
                    self.view.set(str(index), "state", "AI 失败，需重试或手动设置")
                else:
                    path, sheet, _ = self.items[index]
                    self.items[index] = (path, sheet, spec)
                    self.ai_ready.add(index)
                    self._render_item(index)
                    if warnings:
                        problems.append(f"{path.name}/{sheet}：" + "；".join(warnings[:3]))
                self.ai_progress.configure(value=done)
                self.ai_status.set(f"{label} 正在建议列：{done} / {total}")
        except Empty:
            self.after(120, self._poll_ai_batch, total, done, problems, label)

    def _import_ready(self) -> None:
        if any(self.items[index][2] is None for index in self.selected):
            messagebox.showinfo("请确认列设置", "选中的表包含未识别项，请双击手动调整列，或取消勾选。", parent=self)
            return
        selected = [self.items[index][2] for index in sorted(self.selected)]
        if not selected:
            messagebox.showinfo("未选择表", "请至少勾选一个工作表。", parent=self)
            return
        work: list[list] = []
        for spec in selected:
            try:
                work.append([spec, precheck(spec)])
            except Exception:
                work.append([spec, None])
        if any(result is None or result.needs_attention for _spec, result in work):
            PrecheckDialog(self.parent, [(spec, result) for spec, result in work], self._import_prechecked)
            return
        self._import_prechecked([(spec, result) for spec, result in work])

    def _import_prechecked(self, work: list, paused_count: int = 0) -> None:
        if not work:
            return
        paused = paused_count
        selected: list[ImportSpec] = []
        results: dict[tuple[str, str], object] = {}
        for spec, result in work:
            if result is None or result.blocked:
                paused += 1
                continue
            selected.append(spec)
            results[(spec.sheet, str(spec.path))] = result
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
                note = ""
                result = results.get((spec.sheet, str(spec.path)))
                if result is not None and result.stats.excluded_total:
                    note = f"（另排除 {result.stats.excluded_total} 行）"
                imported.append(f'{spec.path.name}/{spec.sheet}：{count} 行{note}')
            except Exception as exc:
                failures.append(f'{spec.path.name}/{spec.sheet}：{exc}')
        self.parent.refresh_all()
        self.destroy()
        differences = sum(item.level == "差异" for item in run_checks(self.parent.store))
        summary = f"成功导入 {len(imported)} 个工作表；当前发现 {differences} 项差异。"
        if paused:
            summary += f"\n{paused} 张表因未解决的疑点暂停（未写入，也不会偷偷跳过坏行）。"
        if duplicate and not replace:
            summary += f"\n跳过 {len(duplicate)} 个已有来源。"
        if failures:
            summary += "\n失败项：\n" + "\n".join(failures)
        if self.skipped:
            summary += f"\n另有 {len(self.skipped)} 个文件读取失败。"
        messagebox.showinfo("导入结果", summary, parent=self.parent)


class ReportAccountsDialog(tk.Toplevel):
    """Let the reviewer choose a browsing scope without changing reconciliation."""

    def __init__(self, parent: "App", label: str, accounts: list, suggested: list[str]):
        super().__init__(parent)
        self.title(f"选择明细科目 · {label}")
        self.geometry("560x480")
        self.transient(parent)
        self.grab_set()
        self.accounts = accounts
        self.chosen = set(suggested)
        self.search = tk.StringVar()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"“{label}”可能涉及多个科目，请勾选查看范围。", wraplength=510).pack(anchor="w")
        ttk.Label(body, text="包含子科目；仅用于浏览，不改变报表核对口径。", style="Hint.TLabel").pack(anchor="w", pady=5)
        query = ttk.Entry(body, textvariable=self.search)
        query.pack(fill="x", pady=5)
        self.search.trace_add("write", lambda *_: self.refresh())
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        def confirm():
            if not self.chosen:
                messagebox.showinfo("请选择科目", "请至少勾选一个科目。", parent=self)
                return
            parent.show_report_accounts(sorted(self.chosen), label)
            self.destroy()
        ttk.Button(footer, text="查看明细", command=confirm).pack(side="right", padx=6)
        self.view = tree(body, [("selected", "选择", 55), ("code", "科目编码", 120), ("name", "科目名称", 300)])
        self.view.bind("<ButtonRelease-1>", self.toggle)
        self.refresh()

    def refresh(self):
        self.view.delete(*self.view.get_children())
        query = self.search.get().strip()
        for account in self.accounts:
            if query and query not in account["code"] and query not in account["name"]:
                continue
            self.view.insert("", "end", iid=account["key"], values=("☑" if account["key"] in self.chosen else "☐", account["code"], account["name"]))

    def toggle(self, event):
        key = self.view.identify_row(event.y)
        if not key:
            return
        if key in self.chosen:
            self.chosen.remove(key)
        else:
            self.chosen.add(key)
        self.view.set(key, "selected", "☑" if key in self.chosen else "☐")


class App(tk.Toplevel):
    def __init__(self, controller: "Application"):
        super().__init__(controller)
        self.controller = controller
        self.title(f"账簿核查 · v{__version__}")
        self.geometry("1160x690")
        self.minsize(820, 520)
        self.resizable(True, True)
        self.store: LedgerStore | None = None
        self.account: str | list[str] | None = None
        self.include_descendants = False
        self.account_label = tk.StringVar(value="全部科目")
        self.period_filter = tk.StringVar(value="全部期间")
        self.period_options: dict[str, tuple[str, str]] = {}
        self.balance_source = tk.StringVar(value="无余额表")
        self.balance_options: dict[str, int] = {}
        self.report_source = {kind: tk.StringVar(value="无报表") for kind in REPORT_KINDS}
        self.report_options: dict[str, dict[str, int]] = {kind: {} for kind in REPORT_KINDS}
        self.search = tk.StringVar()
        self.summary_search = tk.StringVar()
        self.detail_sort = "date"
        self.detail_descending = False
        self.detail_info = tk.StringVar()
        self.mark_search = tk.StringVar()
        self.mark_keys: dict[str, str] = {}
        self.mark_entry_ids: dict[str, int | None] = {}
        self.selected_mark_key: str | None = None
        self.status = tk.StringVar(value="从“文件”导入表格即可开始；新建账套前可选择保存位置。")
        self.column_views: dict[str, list[ttk.Treeview]] = {kind: [] for kind in VIEW_COLUMNS}
        self.voucher_windows: set[tk.Toplevel] = set()
        self._layout()
        self._build_menu()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _layout(self) -> None:
        self.shell = ttk.Frame(self)
        self.shell.pack(fill="both", expand=True)
        self.shell.rowconfigure(0, weight=1)
        self.shell.columnconfigure(1, weight=1)
        self.sidebar = ttk.Frame(self.shell, style="Sidebar.TFrame", width=184)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.pack_propagate(False)
        ttk.Label(self.sidebar, text="DudLedger", style="Brand.TLabel").pack(anchor="w", padx=18, pady=(24, 4))
        ttk.Label(self.sidebar, text="法律尽调 · 账簿核查", style="SidebarHint.TLabel").pack(anchor="w", padx=18, pady=(0, 26))
        self.nav_buttons = []
        self.page_names = ("科目总览", "明细账", "资产负债表", "利润表", "关注凭证", "AI 尽调线索")
        for index, title in enumerate(self.page_names):
            if index in (0, 4):
                ttk.Label(self.sidebar, text="账簿" if index == 0 else "复核", style="SidebarHint.TLabel").pack(anchor="w", padx=20, pady=(14, 8))
            button = ttk.Button(self.sidebar, text=title, style="Nav.TButton", command=lambda page=index: self.tabs.select(page))
            button.pack(fill="x", padx=9, pady=2)
            self.nav_buttons.append(button)
        footer = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        footer.pack(side="bottom", fill="x", padx=9, pady=14)
        ttk.Button(footer, text="偏好设置", style="Nav.TButton", command=self.open_settings).pack(fill="x", pady=2)
        ttk.Button(footer, text="打开账套…", style="Nav.TButton", command=self.open_book).pack(fill="x", pady=2)
        ttk.Separator(footer).pack(fill="x", padx=10, pady=10)
        ttk.Label(footer, text="本地工作区", style="SidebarHint.TLabel").pack(anchor="w", padx=11)
        self.workspace = ttk.Frame(self.shell)
        self.workspace.grid(row=0, column=1, sticky="nsew")
        toolbar = ttk.Frame(self.workspace, padding=(16, 15))
        toolbar.pack(fill="x")
        self.page_title = tk.StringVar(value="开始核查")
        self.page_menu = ttk.Menubutton(toolbar, textvariable=self.page_title)
        self.page_menu.pack(side="left")
        navigation = tk.Menu(self.page_menu, tearoff=False)
        for index, title in enumerate(self.page_names):
            navigation.add_command(label=title, command=lambda page=index: self.tabs.select(page))
        navigation.add_separator()
        navigation.add_command(label="导入记录…", command=lambda: self.open_utility("导入记录"))
        navigation.add_command(label="核对提醒…", command=lambda: self.open_utility("核对提醒"))
        self.page_menu.configure(menu=navigation)
        ttk.Button(toolbar, text="导入表格", command=self.import_file, style="Accent.TButton").pack(side="right")
        self.book_name = tk.StringVar(value="未打开账套")
        ttk.Label(toolbar, textvariable=self.book_name, style="Hint.TLabel").pack(side="right", padx=16)
        ttk.Separator(self.workspace).pack(fill="x", padx=18)
        self.content = ttk.Frame(self.workspace)
        self.content.pack(fill="both", expand=True, padx=18, pady=(12, 0))
        self.tabs = ttk.Notebook(self.content, style="Workspace.TNotebook")
        self.tabs.pack(fill="both", expand=True)
        self.utility_windows = {}
        frames = []
        for title in ("科目总览", "明细账", "资产负债表", "利润表", "核对提醒", "导入记录", "关注凭证", "AI 尽调线索"):
            if title in ("核对提醒", "导入记录"):
                dialog = tk.Toplevel(self)
                dialog.withdraw()
                dialog.title(title)
                dialog.geometry("1000x580")
                dialog.minsize(700, 420)
                dialog.transient(self)
                dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
                self.utility_windows[title] = dialog
                frame = ttk.Frame(dialog)
                frame.pack(fill="both", expand=True)
            else:
                frame = ttk.Frame(self.tabs)
                self.tabs.add(frame, text=title)
            frames.append(frame)
        summary_search_row = ttk.Frame(frames[0], padding=5)
        summary_search_row.pack(fill="x")
        ttk.Label(summary_search_row, text="查找科目").pack(side="left")
        summary_entry = ttk.Entry(summary_search_row, textvariable=self.summary_search)
        summary_entry.pack(side="left", fill="x", expand=True, padx=6)
        summary_entry.bind("<Return>", lambda _e: self.refresh_all())
        ttk.Button(summary_search_row, text="查找", command=self.refresh_all).pack(side="left")
        ttk.Button(summary_search_row, text="清除", command=self.clear_summary_filter).pack(side="left", padx=5)
        balance_top = ttk.Frame(frames[0], padding=5)
        balance_top.pack(fill="x")
        ttk.Label(balance_top, text="双击科目查看明细；点击“展开／收起”打开子科目。", style="Hint.TLabel").pack(side="left")
        ttk.Button(balance_top, text="全部展开", command=lambda: self.set_expansion(True)).pack(side="left", padx=(12, 4))
        ttk.Button(balance_top, text="全部收起", command=lambda: self.set_expansion(False)).pack(side="left")
        summary_more = ttk.Menubutton(balance_top, text="更多 ▾")
        summary_more.pack(side="right")
        summary_menu = tk.Menu(summary_more, tearoff=False)
        summary_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(self, "balance"))
        summary_menu.add_command(label="全部展开", command=lambda: self.set_expansion(True))
        summary_menu.add_command(label="全部收起", command=lambda: self.set_expansion(False))
        summary_menu.add_command(label="记住当前展开", command=self.remember_expansion)
        summary_more.configure(menu=summary_menu)
        self.summary_view = tree(frames[0], VIEW_COLUMNS["balance"])
        self.summary_view.configure(show="tree headings")
        self.summary_view.heading("#0", text="展开 / 收起")
        self.summary_view.column("#0", width=120, minwidth=100, stretch=False)
        self._register_column_view("balance", self.summary_view)
        self.summary_view.bind("<Button-1>", self.toggle_account)
        self.summary_view.bind("<<TreeviewOpen>>", self._after_expansion_change)
        self.summary_view.bind("<<TreeviewClose>>", self._after_expansion_change)
        self.summary_view.bind("<Double-1>", self.open_account)
        detail_top = ttk.Frame(frames[1], padding=5)
        detail_top.pack(fill="x")
        ttk.Label(detail_top, textvariable=self.account_label, wraplength=330, justify="left").pack(side="left")
        ttk.Button(detail_top, text="显示全部科目", command=self.show_all_entries).pack(side="left", padx=10)
        self.detail_more = ttk.Menubutton(detail_top, text="更多 ▾")
        self.detail_more.pack(side="right")
        detail_menu = tk.Menu(self.detail_more, tearoff=False)
        detail_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(self, "detail"))
        detail_menu.add_command(label="导出当前明细 CSV…", command=self.export_csv)
        detail_menu.add_command(label="恢复日期顺序", command=self.reset_detail_sort)
        self.detail_more.configure(menu=detail_menu)
        ttk.Button(detail_top, text="关注凭证 / 写备注", command=self.mark_selected_entry).pack(side="right", padx=6)
        self.detail_controls = ttk.Frame(frames[1], padding=(5, 0, 5, 5))
        self.detail_controls.pack(fill="x")
        ttk.Label(self.detail_controls, text="关键词").pack(side="left")
        detail_entry = ttk.Entry(self.detail_controls, textvariable=self.search, width=16)
        detail_entry.pack(side="left", fill="x", expand=True, padx=6)
        detail_entry.bind("<Return>", lambda _e: self.refresh_details())
        ttk.Label(self.detail_controls, text="期间").pack(side="left")
        self.period_combo = ttk.Combobox(self.detail_controls, textvariable=self.period_filter, values=("全部期间",), state="readonly", width=14)
        self.period_combo.pack(side="left", padx=6)
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._period_changed())
        ttk.Button(self.detail_controls, text="查找", command=self.refresh_details, style="Accent.TButton").pack(side="left")
        ttk.Button(self.detail_controls, text="重置筛选", command=self.clear_filter).pack(side="left", padx=(6, 0))
        ttk.Label(frames[1], textvariable=self.detail_info, style="Hint.TLabel", padding=(6, 2)).pack(fill="x")
        self.detail_view = tree(frames[1], VIEW_COLUMNS["detail"])
        self._register_column_view("detail", self.detail_view)
        self.detail_view.bind("<Double-1>", self.open_voucher)
        for key in ("date", "voucher", "code", "name", "summary", "debit", "credit"):
            self.detail_view.heading(key, command=lambda column=key: self.sort_details(column))
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
            ttk.Label(frame, textvariable=info, padding=(8, 3), foreground="#555", wraplength=760, justify="left").pack(fill="x")
            columns = [("left_item", "项目", 230), ("left_a", "金额 1", 125), ("left_b", "金额 2", 125), ("left_c", "金额 3", 125)]
            if kind == "balance_sheet":
                columns += [("right_item", "右侧项目", 230), ("right_a", "右侧金额 1", 125), ("right_b", "右侧金额 2", 125), ("right_c", "右侧金额 3", 125)]
            columns += [("source", "来源行", 260)]
            self.report_views[kind] = tree(frame, columns)
            self.report_views[kind].bind("<ButtonRelease-1>", lambda event, report_kind=kind: self.open_report_account(report_kind, event))
        check_top = ttk.Frame(frames[4], padding=5)
        check_top.pack(fill="x")
        ttk.Label(check_top, text="这里只列需要核对的线索；金额差异不影响查看原始分录。", style="Hint.TLabel").pack(side="left")
        ttk.Button(check_top, text="设置核对范围", command=self.open_reconciliation).pack(side="right")
        self.check_view = tree(frames[4], [("level", "级别", 70), ("category", "类别", 130), ("subject", "对象", 250), ("detail", "说明", 650)])
        source_top = ttk.Frame(frames[5], padding=5)
        source_top.pack(fill="x")
        ttk.Button(source_top, text="添加表格", command=self.import_file, style="Accent.TButton").pack(side="left")
        ttk.Button(source_top, text="移除所选导入", command=self.delete_selected_source).pack(side="left")
        source_choice = ttk.Frame(frames[5], padding=(5, 2, 5, 6))
        source_choice.pack(fill="x")
        ttk.Label(source_choice, text="总览使用的科目余额表").pack(side="left")
        self.balance_combo = ttk.Combobox(source_choice, textvariable=self.balance_source, values=("无余额表",), state="readonly", width=45)
        self.balance_combo.pack(side="left", padx=8)
        self.balance_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())
        ttk.Label(source_choice, text="切换来源只影响显示，不会合并不同期间。", style="Hint.TLabel").pack(side="left")
        self.source_view = tree(frames[5], [("file", "文件", 220), ("sheet", "工作表", 130), ("kind", "类型", 85), ("rows", "有效行", 80), ("precheck", "导入预检", 170), ("hash", "SHA-256", 220), ("path", "原始路径", 430)])
        mark_top = ttk.Frame(frames[6], padding=5)
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
        ttk.Label(mark_actions, text="双击或点“打开凭证”查看原凭证；选中后可在下方修改注释。", style="Hint.TLabel").pack(side="left")
        ttk.Button(mark_actions, text="打开凭证", command=self.open_selected_mark_voucher).pack(side="left", padx=5)
        ttk.Button(mark_actions, text="取消关注", command=self.remove_selected_mark).pack(side="left", padx=5)
        ttk.Button(mark_actions, text="确认变更凭证", command=self.confirm_selected_mark).pack(side="left", padx=5)
        ttk.Button(mark_actions, text="另存为 MD", command=lambda: self.export_marks(as_new=True)).pack(side="right")
        ttk.Button(mark_actions, text="更新关注凭证 MD", command=self.export_marks, style="Accent.TButton").pack(side="right", padx=5)
        self.mark_view = tree(frames[6], [("status", "状态", 85), ("voucher", "凭证", 175), ("accounts", "涉及科目", 275), ("note", "注释摘要", 360)])
        self.mark_view.bind("<<TreeviewSelect>>", self._load_selected_mark)
        self.mark_view.bind("<Double-1>", self.open_selected_mark_voucher)
        note_frame = ttk.Frame(frames[6], padding=(6, 6))
        note_frame.pack(fill="x")
        ttk.Label(note_frame, text="注释").pack(anchor="w")
        self.mark_note = tk.Text(note_frame, height=4, wrap="word", undo=True, background="#FFFDFB", relief="solid", borderwidth=1)
        self.mark_note.pack(fill="x", pady=(4, 5))
        ttk.Button(note_frame, text="保存注释", command=self.edit_selected_mark).pack(anchor="e")
        from .jev_ui import ReviewPanel
        self.review_panel = ReviewPanel(frames[7], self)
        self.tabs.bind("<<NotebookTabChanged>>", self._page_changed)
        ttk.Label(self.workspace, textvariable=self.status, anchor="w", style="Status.TLabel").pack(fill="x")
        self.welcome = ttk.Frame(self.content)
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        welcome_body = ttk.Frame(self.welcome)
        welcome_body.place(relx=0.5, rely=0.44, anchor="center")
        ttk.Label(welcome_body, text="从一份账簿开始", style="Welcome.TLabel").pack(pady=(0, 14))
        ttk.Label(welcome_body, text="报表、科目、明细与凭证，在一个工作区核查。", style="Hint.TLabel").pack(pady=(0, 28))
        actions = ttk.Frame(welcome_body)
        actions.pack()
        ttk.Button(actions, text="导入 Excel / CSV", style="Accent.TButton", command=self.import_file).pack(side="left", padx=5)
        ttk.Button(actions, text="打开已有账套", command=self.open_book).pack(side="left", padx=5)
        ttk.Label(welcome_body, text="原表保持不变  ·  AI 辅助需确认后启用", style="Hint.TLabel").pack(pady=(22, 0))
        self.bind("<Configure>", self._resize_workspace, add="+")
        self.after_idle(self._resize_workspace)
        self._page_changed()

    def _page_changed(self, _event=None) -> None:
        selected = self.tabs.select()
        if not selected:
            return
        index = self.tabs.index(selected)
        self.page_title.set(self.page_names[index] if self.store else "开始核查")
        for page, button in enumerate(self.nav_buttons):
            button.state(["selected" if page == index else "!selected"])
        if index == 5:
            self.review_panel.refresh()

    def _resize_workspace(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        width = self.winfo_width()
        if width < 10:
            return
        # Preserve the ledger width when the desktop window is made compact.
        sidebar_width = max(184, self.controller.font_size * 13)
        self.sidebar.configure(width=sidebar_width)
        if width < 850 + sidebar_width:
            self.sidebar.grid_remove()
        else:
            self.sidebar.grid()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="（批量）导入表格…", command=self.import_file, accelerator="⌘I")
        files.add_command(label="打开账套…", command=self.open_book, accelerator="⌘O")
        files.add_separator()
        files.add_command(label="在新账套中导入…", command=lambda: self.controller.new_window().import_file())
        files.add_command(label="导入记录…", command=lambda: self.open_utility("导入记录"))
        files.add_command(label="导出导入质量报告…", command=self.export_import_report)
        files.add_separator()
        files.add_command(label="导出当前明细 CSV…", command=self.export_csv)
        files.add_command(label="更新关注凭证 MD…", command=self.export_marks)
        menu.add_cascade(label="文件", menu=files)
        review = tk.Menu(menu, tearoff=False)
        review.add_command(label="科目总览", command=lambda: self.tabs.select(0))
        review.add_command(label="明细账", command=lambda: self.tabs.select(1))
        review.add_command(label="关注凭证", command=lambda: self.tabs.select(4))
        review.add_command(label="AI 尽调线索", command=lambda: (self.review_panel.refresh(), self.tabs.select(5)))
        review.add_command(label="筛选全部凭证…", command=lambda: (self.tabs.select(5), self.review_panel.new_batch()))
        review.add_command(label="尽调主体名单…", command=self.open_related_parties)
        review.add_command(label="核对提醒…", command=lambda: self.open_utility("核对提醒"))
        review.add_command(label="核对范围…", command=self.open_reconciliation)
        menu.add_cascade(label="查看", menu=review)
        options = tk.Menu(menu, tearoff=False)
        options.add_command(label="偏好设置…", command=self.open_settings)
        options.add_command(label="新账套默认文件夹…", command=self.choose_book_folder)
        options.add_command(label="Jev 审查…", command=self.open_settings)
        menu.add_cascade(label="设置", menu=options)
        self.configure(menu=menu)
        for dialog in self.utility_windows.values():
            dialog.configure(menu=menu)
        self.bind("<Command-i>", lambda _event: self.import_file())
        self.bind("<Command-o>", lambda _event: self.open_book())

    def open_utility(self, title: str) -> None:
        dialog = self.utility_windows[title]
        dialog.deiconify()
        dialog.lift()

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

    def open_review_rules(self) -> None:
        if not self.store:
            messagebox.showinfo("请打开账套", "规则保存在当前账套，请先打开账套。", parent=self)
            return
        from .jev_ui import ReviewRulesDialog
        ReviewRulesDialog(self)

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
        self.review_panel.cancel()
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
        name = path.stem
        self.book_name.set(name if len(name) <= 24 else name[:23] + "…")
        self.welcome.place_forget()
        for kind, views in self.column_views.items():
            self.column_views[kind] = [view for view in views if view.winfo_exists()]
            for view in self.column_views[kind]:
                view.configure(displaycolumns=visible_columns(kind, self.store.display_columns(kind)))
        self.account = None
        self.account_label.set("全部科目")
        self.period_filter.set("全部期间")
        self.search.set("")
        self.summary_search.set("")
        self.include_descendants = False
        self.balance_source.set("无余额表")
        for choice in self.report_source.values():
            choice.set("无报表")
        self.title(f"账簿核查 · {path.name}")
        self.refresh_all()
        self.review_panel.refresh()
        self.tabs.select(1 if self.store.setting("home_view", "summary") == "detail" else 0)
        self._page_changed()

    def import_file(self) -> None:
        chosen = filedialog.askopenfilenames(parent=self, title="选择表格（可多选文件）", filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.xls *.csv"), ("所有文件", "*")])
        if chosen:
            if not self.store:
                try:
                    folder = self.controller.book_folder
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        folder = next((candidate for candidate in (folder, *folder.parents) if candidate.is_dir()), Path.home())
                    name = Path(chosen[0]).stem[:80] or "新账套"
                    index = 1
                    while (folder / f"{name}{'' if index == 1 else '-' + str(index)}.sqlite").exists():
                        index += 1
                    default_name = f"{name}{'' if index == 1 else '-' + str(index)}.sqlite"
                    while True:
                        chosen_book = filedialog.asksaveasfilename(
                            parent=self, title="新账套保存在哪里？可在此调整文件夹和名称",
                            initialdir=str(folder), initialfile=default_name,
                            defaultextension=".sqlite", filetypes=[("账套文件", "*.sqlite")])
                        if not chosen_book:
                            return
                        path = Path(chosen_book).expanduser()
                        if path.suffix.lower() != ".sqlite":
                            messagebox.showerror("文件格式不对", "账套文件名请以 .sqlite 结尾。", parent=self)
                            continue
                        if path.exists():
                            messagebox.showerror("账套已存在", "请选择其他文件名；已有账套请用“打开账套”。", parent=self)
                            folder, default_name = path.parent, path.name
                            continue
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.touch(exist_ok=False)
                        break
                    self.controller.open_book(path, self)
                except OSError as exc:
                    messagebox.showerror("无法新建账套", f"{exc}\n请在保存窗口选择其他位置。", parent=self)
                    return
                if not self.store:
                    return
                self.controller.preferences['book_folder'] = str(path.parent.resolve())
                self.controller._save_preferences()
            BatchImportDialog(self, [Path(path) for path in chosen])

    def choose_book_folder(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="选择新账套默认保存文件夹", initialdir=str(self.controller.book_folder))
        if chosen:
            self.controller.preferences['book_folder'] = str(Path(chosen).resolve())
            self.controller._save_preferences()
            self.status.set(f"新账套将保存在：{self.controller.book_folder}")

    def delete_selected_source(self) -> None:
        if not self.store or not self.source_view.selection():
            messagebox.showinfo("先选择表格", "请在导入记录中选中要移除的工作表。", parent=self)
            return
        source_id = int(self.source_view.selection()[0])
        source = next((row for row in self.store.sources() if row["id"] == source_id), None)
        if source is None:
            self.refresh_all()
            return
        prompt = f'从当前账套移除“{source["filename"]} / {source["sheet"]}”的 {source["row_count"]} 行导入数据？\n\n原始文件不会删除；相关关注凭证的注释会保留，无法匹配时提示复核。'
        if not messagebox.askyesno("移除导入记录", prompt, parent=self):
            return
        self.store.delete_source(source_id)
        self.refresh_all()
        self.status.set(f'已从账套移除 {source["filename"]} / {source["sheet"]}；原始文件未改动。')

    def export_import_report(self) -> None:
        if not self.store:
            messagebox.showinfo("先打开账套", "请先新建或打开本地账套。", parent=self)
            return
        chosen = filedialog.asksaveasfilename(parent=self, title="导出导入质量报告", defaultextension=".md", filetypes=[("Markdown", "*.md"), ("文本", "*.txt")])
        if not chosen:
            return
        try:
            count = self.store.export_import_report(Path(chosen))
            messagebox.showinfo("导出完成", f"已导出 {count} 条导入记录的中文质量报告。", parent=self)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def export_csv(self) -> None:
        if not self.store:
            return
        chosen = filedialog.asksaveasfilename(parent=self, title="导出当前科目明细", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if chosen:
            try:
                year, period = self._selected_period()
                count = self.store.export_entries(Path(chosen), self.account, self.search.get().strip(), year, period, self.include_descendants, self.detail_sort, self.detail_descending)
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
        self.open_voucher(None, edit_attention=True)

    def refresh_marks(self, select_key: str | None = None) -> None:
        self.mark_view.delete(*self.mark_view.get_children())
        self.mark_keys = {}
        self.mark_entry_ids = {}
        self.selected_mark_key = None
        self.mark_note.delete("1.0", "end")
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
            self.mark_entry_ids[iid] = lines[0]["id"] if lines else None
            self.mark_view.insert("", "end", iid=iid, values=(mark["status"], label, accounts, mark["note"].replace("\n", " ")[:160]))
            if mark["key"] == select_key:
                self.mark_view.selection_set(iid)
                self.mark_view.see(iid)
                self._load_selected_mark()

    def open_selected_mark_voucher(self, _event: object = None) -> None:
        selection = self.mark_view.selection()
        if not selection:
            messagebox.showinfo("请选择凭证", "请先在关注凭证列表中选中一张凭证。", parent=self)
            return
        entry_id = self.mark_entry_ids.get(selection[0])
        if entry_id is None:
            messagebox.showinfo("无法打开", "原凭证已无法匹配（明细可能被重新导入），请在明细账中重新关注。", parent=self)
            return
        self.open_voucher(None, entry_id=entry_id)

    def _selected_mark_key(self) -> str | None:
        selection = self.mark_view.selection()
        return self.mark_keys.get(selection[0]) if selection else None

    def _load_selected_mark(self, _event: object = None) -> None:
        key = self._selected_mark_key()
        if key == self.selected_mark_key:
            return
        self.selected_mark_key = key
        self.mark_note.delete("1.0", "end")
        if key and self.store:
            row = self.store.db.execute("SELECT note FROM voucher_marks WHERE voucher_key=?", (key,)).fetchone()
            if row:
                self.mark_note.insert("1.0", row["note"])

    def edit_selected_mark(self) -> None:
        key = self._selected_mark_key()
        if not key or not self.store:
            return
        update_mark_note(self.store, key, self.mark_note.get("1.0", "end-1c"))
        self.refresh_marks(select_key=key)
        self.status.set("注释已保存在当前账套；更新 MD 时会使用最新内容。")

    def confirm_selected_mark(self) -> None:
        key = self._selected_mark_key()
        if not key or not self.store:
            return
        mark = next((item for item in list_marks(self.store) if item["key"] == key), None)
        if mark is None or mark["status"] == "未匹配":
            messagebox.showinfo("无法确认", "原凭证已无法匹配，请从当前明细重新关注。", parent=self)
            return
        if mark["status"] == "内容变化" and not messagebox.askyesno("确认凭证内容", "当前凭证与标注时的内容不同。已核对现有分录并确认继续关注吗？", parent=self):
            return
        save_mark_by_key(self.store, key, self.mark_note.get("1.0", "end-1c"))
        self.refresh_details()
        self.refresh_marks(select_key=key)

    def remove_selected_mark(self) -> None:
        key = self._selected_mark_key()
        if key and self.store and messagebox.askyesno("取消关注", "确定取消关注并删除这条注释吗？", parent=self):
            remove_mark(self.store, key)
            self.refresh_details()
            self.refresh_marks()

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

    def clear_summary_filter(self) -> None:
        self.summary_search.set("")
        self.refresh_all()

    def clear_filter(self) -> None:
        self.search.set("")
        self.period_filter.set("全部期间")
        self.refresh_details()

    def reset_detail_sort(self) -> None:
        self.detail_sort = "date"
        self.detail_descending = False
        self.refresh_details()

    def sort_details(self, column: str) -> None:
        self.detail_descending = not self.detail_descending if column == self.detail_sort else False
        self.detail_sort = column
        self.refresh_details()

    def show_all_entries(self) -> None:
        self.account = None
        self.include_descendants = False
        self.account_label.set("全部科目")
        self.refresh_details()

    def _selected_period(self) -> tuple[str | None, str | None]:
        return self.period_options.get(self.period_filter.get(), (None, None))

    def _period_changed(self) -> None:
        self.refresh_details()

    def refresh_all(self) -> None:
        for view in (self.summary_view, self.detail_view, self.check_view, self.source_view, self.mark_view):
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
        summary_rows = self.store.summary(self.summary_search.get().strip(), selected_balance)
        by_code = {row["code"]: row for row in summary_rows if row["code"]}
        parent_keys = {}
        counts = {row["key"]: row["detail_count"] for row in summary_rows}
        for row in summary_rows:
            code = row["code"]
            parent_code = next((code[:size] for size in range(len(code) - 1, 0, -1) if code[:size] in by_code), None) if code else None
            parent_keys[row["key"]] = by_code[parent_code]["key"] if parent_code else ""
        for row in summary_rows:
            ancestor = parent_keys[row["key"]]
            while ancestor:
                counts[ancestor] += row["detail_count"]
                ancestor = parent_keys[ancestor]
        expansion_mode = self.store.setting("account_expansion_mode", "none")
        expanded = set(json.loads(self.store.setting("expanded_accounts", "[]"))) if expansion_mode == "custom" else set()
        for row in summary_rows:
            # When no supplied balance table exists, show computed period movement only.
            pd = row["reported_debit"] if row["balance_count"] else row["detail_debit"]
            pc = row["reported_credit"] if row["balance_count"] else row["detail_credit"]
            side_tag = "debit_row" if row["closing_debit"] and not row["closing_credit"] else "credit_row" if row["closing_credit"] and not row["closing_debit"] else ""
            tags = tuple(filter(None, ("account_parent" if counts[row["key"]] > row["detail_count"] else "", side_tag)))
            self.summary_view.insert(parent_keys[row["key"]], "end", iid=row["key"], open=expansion_mode == "all" or row["key"] in expanded,
                                     values=(row["code"], row["name"], amount(row["opening_debit"]), amount(row["opening_credit"]), amount(pd), amount(pc), amount(row["year_debit"]), amount(row["year_credit"]), amount(row["closing_debit"]), amount(row["closing_credit"]), counts[row["key"]]), tags=tags)
        self._expansion_labels()
        self.refresh_details()
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
            record = self.store.precheck_for_source(row["id"])
            precheck_label = record["verdict"] if record is not None else "无历史预检报告（旧版导入）"
            self.source_view.insert("", "end", iid=str(row["id"]), values=(row["filename"], row["sheet"], kind_label, row["row_count"], precheck_label, row["sha256"], row["original_path"]))
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
        self.report_info[kind].set(f'{source["filename"]} · {source["sheet"]} · {source["row_count"]} 行 · 点击项目名称查看明细（全部期间）')
        for row in self.store.report_rows(source_id):
            values = [row["left_item"], report_amount(row["left_amount_1_cents"]), report_amount(row["left_amount_2_cents"]), report_amount(row["left_amount_3_cents"])]
            if kind == "balance_sheet":
                values.extend((row["right_item"], report_amount(row["right_amount_1_cents"]), report_amount(row["right_amount_2_cents"]), report_amount(row["right_amount_3_cents"])))
            values.append(f'{source["filename"]}/{source["sheet"]}:{row["row_no"]}')
            view.insert("", "end", iid=str(row["row_no"]), values=values)

    @staticmethod
    def _report_item_name(value: str) -> str:
        name = re.sub(r"\s+", "", value)
        name = re.sub(r"^[一二三四五六七八九十百\d]+[、.．：:]", "", name)
        name = re.sub(r"^(?:其中|加|减)[：:]", "", name)
        return name

    def open_report_account(self, kind: str, event) -> None:
        if not self.store:
            return
        view = self.report_views[kind]
        item = view.identify_row(event.y)
        column = view.identify_column(event.x)
        if not item or not column or column == "#0":
            return
        # identify_column uses the displayed order, including hidden amount columns.
        displayed = view.cget("displaycolumns")
        index = int(column[1:]) - 1
        if index >= len(displayed) or displayed[index] not in ("left_item", "right_item"):
            return
        side = "left" if displayed[index] == "left_item" else "right"
        source_id = self.report_options[kind].get(self.report_source[kind].get())
        row = next((row for row in self.store.report_rows(source_id) if str(row["row_no"]) == item), None)
        if row is None or not row[f"{side}_item"].strip():
            return
        label = row[f"{side}_item"]
        accounts = self.store.db.execute("SELECT key,code,name FROM accounts ORDER BY code,name").fetchall()
        existing = {account["key"] for account in accounts}
        mappings = [mapping for mapping in load_plan(self.store)["mappings"]
                    if mapping.get("kind") == kind and mapping.get("source_id") == source_id
                    and mapping.get("row_no") == row["row_no"] and mapping.get("side") == side]
        keys = sorted({key for mapping in mappings for key in mapping.get("accounts", [])})
        if keys and set(keys) <= existing:
            self.show_report_accounts(keys, label, include_descendants=False)
            return
        name = self._report_item_name(label)
        exact = [account["key"] for account in accounts if self._report_item_name(account["name"]) == name]
        if len(exact) == 1:
            self.show_report_accounts(exact, label)
            return
        suggestions = {"货币资金": {"库存现金", "现金", "银行存款", "其他货币资金"},
                       "营业收入": {"主营业务收入", "其他业务收入"},
                       "营业成本": {"主营业务成本", "其他业务成本"}}
        suggested = sorted(set(exact) | {account["key"] for account in accounts if account["name"] in suggestions.get(name, set())})
        if suggested:
            self.show_report_accounts(suggested, label)
            return
        ReportAccountsDialog(self, label, accounts, [])

    def show_report_accounts(self, keys: list[str], label: str, include_descendants: bool = True) -> None:
        self.account = keys
        self.include_descendants = include_descendants
        names = [row["name"] for row in self.store.db.execute("SELECT key,name FROM accounts") if row["key"] in keys]
        scope = "、".join(names)
        if len(scope) > 30:
            scope = scope[:30] + f"…（{len(keys)} 个科目）"
        self.account_label.set(f"{label} → {scope}" + (" · 含子科目" if include_descendants else ""))
        self.search.set("")
        self.period_filter.set("全部期间")
        self.refresh_details()
        self.tabs.select(1)

    def refresh_details(self) -> None:
        self.detail_view.delete(*self.detail_view.get_children())
        if not self.store:
            return
        year, period = self._selected_period()
        rows_found = self.store.entries(account=self.account, search=self.search.get().strip(), fiscal_year=year, period=period, include_descendants=self.include_descendants, sort_by=self.detail_sort, descending=self.detail_descending)
        marked_keys = {row["voucher_key"] for row in self.store.db.execute("SELECT voucher_key FROM voucher_marks")}
        parties = self.store.related_parties()
        for row in rows_found:
            side_tag = "debit_row" if row["debit_cents"] and not row["credit_cents"] else "credit_row" if row["credit_cents"] and not row["debit_cents"] else ""
            marked = voucher_key(row) in marked_keys
            self.detail_view.insert("", "end", iid=str(row["id"]), values=("●" if marked else "", row["entry_date"], row["fiscal_year"], row["period"], f'{row["voucher_type"]}{row["voucher_no"]}', row["code"], row["name"], row["summary"], row["counterparty"], related_party_match(row, parties), row["auxiliary"], row["currency"], row["original_amount"], amount(row["debit_cents"]), amount(row["credit_cents"]), row["maker"], f'{row["filename"]}/{row["sheet"]}:{row["row_no"]}'), tags=("marked",) if marked else ((side_tag,) if side_tag else ()))
        for key, title, _ in VIEW_COLUMNS["detail"]:
            arrow = (" ↓" if self.detail_descending else " ↑") if key == self.detail_sort else ""
            self.detail_view.heading(key, text=title + arrow)
        count_label = "已显示前 3000 条，请缩小范围；导出包含全部匹配项" if len(rows_found) == 3000 else f"{len(rows_found)} 条明细"
        self.detail_info.set(f"{count_label} · {self.period_filter.get()} · 点击日期、科目、凭证或借贷表头排序；双击一行看凭证")

    def open_account(self, _event: object) -> None:
        if _event is not None and self.summary_view.identify_column(_event.x) == '#0':
            return 'break'
        selection = self.summary_view.selection()
        if not selection:
            return
        self.account = selection[0]
        self.include_descendants = self.account.startswith("C:")
        self.search.set("")
        self.period_filter.set("全部期间")
        values = self.summary_view.item(selection[0], "values")
        self.account_label.set(f"{values[0]} {values[1]}".strip() + (" · 含子科目" if self.include_descendants else ""))
        self.refresh_details()
        self.tabs.select(1)

    def toggle_account(self, event):
        view = self.summary_view
        item = view.identify_row(event.y)
        if item and view.identify_column(event.x) == '#0' and view.get_children(item):
            view.selection_set(item)
            view.focus(item)
            view.item(item, open=not view.item(item, 'open'))
            self._expansion_labels()
            self._save_expansion()
            return 'break'

    def _after_expansion_change(self, _event: object = None) -> None:
        self.after_idle(self._expansion_labels)
        self.after_idle(self._save_expansion)

    def _save_expansion(self) -> None:
        """Persist which rows are open so refreshes and restarts keep the view."""
        if not self.store:
            return
        expanded = []
        def visit(parent: str = "") -> None:
            for item in self.summary_view.get_children(parent):
                if self.summary_view.item(item, "open"):
                    expanded.append(item)
                visit(item)
        visit()
        self.store.save_setting("expanded_accounts", json.dumps(expanded, ensure_ascii=False))
        self.store.save_setting("account_expansion_mode", "custom")

    def _expansion_labels(self):
        def visit(parent=''):
            for item in self.summary_view.get_children(parent):
                children = self.summary_view.get_children(item)
                self.summary_view.item(item, text=('收起' if self.summary_view.item(item, 'open') else '展开') if children else '')
                visit(item)
        visit()
        self.summary_view.dashed_grid.schedule()

    def set_expansion(self, expanded: bool) -> None:
        if not self.store:
            return
        def visit(parent: str = "") -> None:
            for item in self.summary_view.get_children(parent):
                self.summary_view.item(item, open=expanded)
                visit(item)
        visit()
        self._expansion_labels()
        self.store.save_setting("account_expansion_mode", "all" if expanded else "none")

    def remember_expansion(self) -> None:
        if not self.store:
            return
        self._save_expansion()
        self.status.set("已记住当前展开的科目；刷新或重启后仍保持。")

    def open_voucher(self, _event: object, entry_id: int | None = None, *, edit_attention: bool = False) -> None:
        if not self.store:
            return
        selection = self.detail_view.selection()
        if entry_id is None:
            if not selection:
                return
            entry_id = int(selection[0])
        lines = self.store.voucher_lines(entry_id)
        if not lines:
            return
        if self.store.setting("voucher_open_mode", "replace") != "multiple":
            for old in list(self.voucher_windows):
                if old.winfo_exists():
                    old.destroy()
            self.voucher_windows.clear()
        target = lines[0]
        existing_mark = mark_for_entry(self.store, entry_id)
        dialog = tk.Toplevel(self)
        dialog.ledger_app = self
        self.voucher_windows.add(dialog)
        dialog.bind("<Destroy>", lambda event: self.voucher_windows.discard(dialog) if event.widget is dialog else None)
        dialog.title(f'凭证 · {target["fiscal_year"]}年{target["period"]}期 {target["voucher_type"]}{target["voucher_no"]}')
        dialog.minsize(620, 240)
        voucher_top = ttk.Frame(dialog, padding=10)
        voucher_top.pack(fill="x")
        ttk.Label(voucher_top, text="已导入的凭证分录").pack(side="left")
        more = ttk.Menubutton(voucher_top, text="显示设置")
        more.pack(side="right")
        display_menu = tk.Menu(more, tearoff=False)
        display_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(dialog, "voucher"))
        display_menu.add_command(label="还原默认格式", command=self.reset_voucher_format)
        more.configure(menu=display_menu)
        view = tree(dialog, VIEW_COLUMNS["voucher"])
        view.configure(height=min(8, max(2, len(lines))))
        self._register_column_view("voucher", view)
        for key, width in self.store.voucher_widths().items():
            if key in view["columns"]:
                view.column(key, width=width)
        view.bind("<ButtonRelease-1>", lambda _e, widget=view: self._save_voucher_widths(widget))
        for line in lines:
            side_tag = "debit_row" if line["debit_cents"] and not line["credit_cents"] else "credit_row" if line["credit_cents"] and not line["debit_cents"] else ""
            view.insert("", "end", values=(line["code"], line["name"], line["summary"], line["auxiliary"], line["currency"], line["original_amount"], amount(line["debit_cents"]), amount(line["credit_cents"]), f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'), tags=("marked",) if existing_mark else ((side_tag,) if side_tag else ()))
        debit = sum(line["debit_cents"] for line in lines)
        credit = sum(line["credit_cents"] for line in lines)
        ttk.Label(dialog, text=f"{len(lines)} 行分录   借方 {amount(debit) or '0.00'}   贷方 {amount(credit) or '0.00'}   差额 {amount(debit-credit) or '0.00'}", padding=(12, 7)).pack(anchor="w")
        quick_actions = ttk.Frame(dialog, padding=(12, 0, 12, 8))
        quick_actions.pack(fill="x")
        note_area = ttk.Frame(dialog, padding=(12, 5, 12, 10))
        attention = tk.BooleanVar(value=bool(existing_mark))
        ttk.Checkbutton(note_area, text="关注这张凭证", variable=attention).pack(anchor="w")
        ttk.Label(note_area, text="注释会随关注记录保存在当前账套；再次导出 MD 时使用最新内容。", style="Hint.TLabel").pack(anchor="w", pady=(2, 5))
        editor = tk.Text(note_area, height=4, wrap="word", undo=True, background="#FFFDFB", relief="solid", borderwidth=1)
        editor.pack(fill="x")
        if existing_mark:
            editor.insert("1.0", existing_mark["note"])
        actions = ttk.Frame(note_area)
        actions.pack(fill="x", pady=(7, 0))
        feedback = tk.StringVar()
        ttk.Label(actions, textvariable=feedback, style="Hint.TLabel").pack(side="left")
        def save_attention() -> None:
            try:
                current = mark_for_entry(self.store, entry_id)
                if attention.get():
                    if current:
                        current_status = next((item["status"] for item in list_marks(self.store) if item["key"] == current["voucher_key"]), "正常")
                        if current_status == "内容变化" and not messagebox.askyesno("确认凭证内容", "凭证内容与原标注时不同。确认已核对当前分录并继续关注吗？", parent=dialog):
                            return
                    save_mark(self.store, entry_id, editor.get("1.0", "end-1c"))
                    feedback.set("已保存至关注凭证")
                elif current:
                    remove_mark(self.store, current["voucher_key"])
                    feedback.set("已取消关注")
                else:
                    feedback.set("未关注此凭证")
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            for item in view.get_children():
                view.item(item, tags=("marked",) if attention.get() else ())
            self.refresh_details()
            self.refresh_marks(select_key=voucher_key(target) if attention.get() else None)
        ttk.Button(actions, text="保存关注与注释", command=save_attention, style="Accent.TButton").pack(side="right")
        collapsed_label = "查看 / 编辑关注备注" if existing_mark else "关注这张凭证"
        note_height = 0
        def toggle_notes() -> None:
            nonlocal note_height
            width, height = dialog.winfo_width(), dialog.winfo_height()
            if note_area.winfo_manager():
                note_area.pack_forget()
                dialog.minsize(620, 240)
                dialog.geometry(f"{width}x{max(compact_height, height - note_height)}")
                toggle.configure(text="查看 / 编辑关注备注" if mark_for_entry(self.store, entry_id) else "关注这张凭证")
                view.focus_set()
            else:
                attention.set(True)
                note_area.pack(fill="x")
                dialog.update_idletasks()
                note_height = note_area.winfo_reqheight()
                dialog.minsize(620, 240 + note_height)
                dialog.geometry(f"{width}x{min(dialog.winfo_screenheight() - 100, height + note_height)}")
                toggle.configure(text="收起备注")
                editor.focus_set()
        toggle = ttk.Button(quick_actions, text=collapsed_label, command=toggle_notes)
        toggle.pack(side="left")
        ttk.Button(quick_actions, text="关闭", command=dialog.destroy).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.update_idletasks()
        compact_height = min(440, max(240, dialog.winfo_reqheight()))
        dialog.geometry(f"760x{compact_height}")
        dialog.update_idletasks()
        if edit_attention:
            toggle_notes()
        else:
            view.focus_set()

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
    @property
    def book_folder(self) -> Path:
        saved = self.preferences.get('book_folder')
        return Path(saved).expanduser() if isinstance(saved, str) and saved else Path.home() / 'Documents' / 'DudLedger' / '账套'

    def __init__(self):
        super().__init__()
        self._configure_theme()
        self.preferences_path = Path.home() / ".dudledger" / "preferences.json"
        default_size = abs(tkfont.nametofont("TkDefaultFont").cget("size"))
        try:
            self.preferences = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            self.preferences = {}
        if not isinstance(self.preferences, dict):
            self.preferences = {}
        saved_size = self.preferences.get("font_size", default_size)
        if type(saved_size) is not int or not 9 <= saved_size <= 20:
            saved_size = default_size
        self.scroll_mode = self.preferences.get("scroll_mode", "system")
        if self.scroll_mode not in ("system", "gentle", "quick"):
            self.scroll_mode = "system"
        self.show_grid = self.preferences.get("show_grid", True) is not False
        self.ai_provider = self.preferences.get("ai_provider", "jev")
        if self.ai_provider not in PROVIDER_LABELS:
            self.ai_provider = "jev"
        self.font_size = default_size
        self.set_font_size(saved_size, persist=False)
        self.withdraw()
        self.windows: set[App] = set()
        self.book_windows: dict[Path, App] = {}
        self.new_window()

    def _configure_theme(self) -> None:
        configure_theme(self)

    def set_font_size(self, size: int, persist: bool = True) -> None:
        if type(size) is not int or not 9 <= size <= 20:
            raise ValueError("字体大小须在 9—20 之间")
        for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkHeadingFont", "TkMenuFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        ttk.Style(self).configure("Treeview", rowheight=max(28, size + 16))
        self.intro_font.configure(size=size + 3)
        self.welcome_font.configure(size=size + 13)
        self.font_size = size
        for window in getattr(self, "windows", ()):
            window._resize_workspace()
            pending = [window]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                grid = getattr(widget, "dashed_grid", None)
                if grid is not None:
                    grid.schedule()
        if persist:
            self._save_preferences()

    def set_scroll_mode(self, mode: str) -> None:
        if mode not in ("system", "gentle", "quick"):
            raise ValueError("未知滚动速度")
        self.scroll_mode = mode
        self._save_preferences()

    def set_ai_provider(self, provider: str) -> None:
        if provider not in PROVIDER_LABELS:
            raise ValueError("未知的 AI 建议模型")
        self.preferences["ai_provider"] = provider
        self.ai_provider = provider
        self._save_preferences()

    def set_grid_lines(self, enabled: bool) -> None:
        self.show_grid = bool(enabled)
        for window in self.windows:
            pending = [window]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                grid = getattr(widget, "dashed_grid", None)
                if grid is not None:
                    grid.set_enabled(self.show_grid)
        self._save_preferences()

    def _save_preferences(self) -> None:
        self.preferences.update(font_size=self.font_size, scroll_mode=self.scroll_mode, show_grid=self.show_grid)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.preferences_path.parent, prefix=".preferences.", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self.preferences, handle)
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
