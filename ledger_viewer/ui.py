"""Tk desktop interface; Jev networking requires an explicit preview confirmation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from threading import Event
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__, DISPLAY_VERSION
from . import theme
from .agent_import import handoff_text
from .import_cascade import redact_error
from .import_preview import PRIVACY_NOTICE
from .import_work import inventory, prepare_sheets, preview_data, requires_background
from .import_review import review_sheet, suggest_columns
from .background import BackgroundTasks, Cancelled, check_cancelled
from .checks import run_checks
from .deepseek_mapping import delete_api_key as deepseek_delete_api_key, get_api_key as deepseek_get_api_key, key_memory_only as deepseek_memory_only, save_api_key as deepseek_save_api_key, suggest_mapping as deepseek_suggest_mapping
from .bailian_review import (DEFAULT_ENDPOINT as BAILIAN_ENDPOINT, delete_api_key as bailian_delete_api_key,
    get_api_key as bailian_get_api_key, key_memory_only as bailian_memory_only,
    save_api_key as bailian_save_api_key, validate_endpoint as validate_bailian_endpoint)
from .detail_filters import EntryCondition
from .shortcuts import VoucherShortcuts, configured_shortcuts, validate_shortcuts, SHORTCUT_LABELS, DEFAULT_SHORTCUTS, event_shortcut
from .detail_filter_ui import DetailFilterDialog, filter_arrow, heading_filter_column
from .grid_lines import DashedGrid
from .importer import REPORT_KINDS, ImportSpec, classify_sheet, is_cashflow_sheet, file_hash, format_money, guess_header, guess_mapping, header_for_row, rows, sheet_names, text_value
from .jev_mapping import delete_api_key, get_api_key, key_memory_only, save_api_key
from .navigation import NavigationTooltip, navigation_icon
from .empty_state import EmptyState
from .widgets import RoundedButton, RoundedFrame
from .window_position import center_window
from .precheck import SEVERITY_CRITICAL, SEVERITY_WARNING, PrecheckStats, precheck
from .reconciliation import empty_plan, load_plan, save_plan
from .related_parties import PARTY_ROLES, parse_party_lines, party_entries_from_file, build_party_index, indexed_party_match
from .store import ENTRY_SORT_COLUMNS, LedgerStore
from .table_layout import WorkspaceTableFit
from .theme import configure_theme
from .table_tools import TableTools
from .voucher_selection import VoucherSelection, CellTooltip, selection_bar
from .voucher_summary import read_marks, summaries_for_keys
from .workbench import NoteHub, RightWorkbench, VoucherActions
from .account_navigation import AccountNavigation
from . import issue_store
from .voucher_notes import export_marks_md, list_marks, mark_for_entry, remove_mark, save_mark_by_key, source_account, voucher_key


FIELD_LABELS = {
    "date": "日期", "fiscal_year": "会计年度", "period": "期间", "voucher_no": "凭证号", "voucher_type": "凭证字", "account_code": "科目编码",
    "account_name": "科目名称", "summary": "摘要", "debit": "借方金额", "credit": "贷方金额", "amount": "金额（单列）",
    "counterparty": "往来单位", "auxiliary": "辅助核算", "opening_debit": "期初借方",
    "opening_credit": "期初贷方", "period_debit": "本期借方", "period_credit": "本期贷方",
    "year_debit": "本年累计借方", "year_credit": "本年累计贷方",
    "closing_debit": "期末借方", "closing_credit": "期末贷方",
    "opening_balance": "期初余额", "closing_balance": "期末余额", "direction": "借贷/余额方向",
    "currency": "币别", "original_amount": "原币金额", "maker": "制单人",
    "left_item": "左侧项目", "left_amount_1": "左侧金额 1", "left_amount_2": "左侧金额 2", "left_amount_3": "左侧金额 3",
    "right_item": "右侧项目", "right_amount_1": "右侧金额 1", "right_amount_2": "右侧金额 2", "right_amount_3": "右侧金额 3",
}

REPORT_LABELS = {"balance_sheet": "资产负债表", "income_statement": "利润表"}

PROVIDER_LABELS = {"deepseek": "DeepSeek Flash"}


def ai_tools(controller) -> tuple[str, object, object]:
    """Import recognition always uses DeepSeek Flash, including old preferences."""
    return "DeepSeek Flash", deepseek_suggest_mapping, deepseek_get_api_key


def suggest_for_import(path, sheet, suggest, api_key, cancelled=lambda: False, *, header_row=None, kind=None):
    return suggest_columns(path, sheet, suggest or deepseek_suggest_mapping, api_key, cancelled,
                           header_row=header_row, kind=kind)


def copy_agent_task(dialog, items):
    if not items:
        messagebox.showinfo("未选择表", "请先选择要交给 Agent 的工作表。", parent=dialog)
        return
    content = handoff_text(items, dialog.parent.controller.import_book_folder(Path(items[0][0])))
    dialog.clipboard_clear()
    dialog.clipboard_append(content)
    messagebox.showinfo("已复制导入任务", "粘贴给能访问本机文件和运行命令的 AI Agent，即可按导入技能检查原表并创建新账套。\n任务包含本机路径和所选工作表名，不包含密钥。", parent=dialog)

VIEW_COLUMNS = {
    "balance": [("code", "科目编码", 125), ("name", "科目名称", 200), ("od", "期初借方", 115), ("oc", "期初贷方", 115), ("pd", "本期借方", 115), ("pc", "本期贷方", 115), ("yd", "本年累计借方", 125), ("yc", "本年累计贷方", 125), ("cd", "期末借方", 115), ("cc", "期末贷方", 115), ("n", "明细行数", 80)],
    "detail": [("marked", "关注", 55), ("date", "日期", 105), ("year", "年度", 70), ("period", "期间", 65), ("voucher", "凭证", 120), ("code", "科目编码", 110), ("name", "科目名称", 180), ("summary", "摘要", 250), ("counterparty", "往来单位", 145), ("related", "用户标注关联方", 145), ("aux", "核算维度", 145), ("currency", "币别", 80), ("original", "原币金额", 100), ("debit", "借方", 105), ("credit", "贷方", 105), ("maker", "制单人", 90), ("source", "来源行", 200)],
    "voucher": [("code", "科目编码", 90), ("name", "科目名称", 125), ("summary", "摘要", 190), ("aux", "核算维度", 140), ("currency", "币别", 75), ("original", "原币金额", 100), ("debit", "借方", 90), ("credit", "贷方", 90), ("source", "来源行", 135)],
}

DEFAULT_COLUMNS = {
    "balance": ["code", "name", "pd", "pc", "cd", "cc", "n"],
    "detail": ["date", "voucher", "name", "summary", "counterparty", "debit", "credit", "marked"],
    "voucher": ["code", "name", "summary", "debit", "credit"],
}

DETAIL_FILTER_LABELS = {key: title for key, title, _ in VIEW_COLUMNS["detail"] if key in ("summary", "date", "debit", "credit", "original")}


MONEY_COLUMN_KEYS = {"od", "oc", "pd", "pc", "yd", "yc", "cd", "cc", "debit", "credit", "original", "left_a", "left_b", "left_c", "right_a", "right_b", "right_c", "a", "b"}


def visible_columns(kind: str, saved: list[str] | None) -> list[str]:
    """Keep known columns in table order; use the default for stale preferences."""
    available = [key for key, _, _ in VIEW_COLUMNS[kind] if kind != "voucher" or key != "source"]
    if saved is None:
        return DEFAULT_COLUMNS[kind].copy()
    selected = set(saved)
    result = [key for key in available if key in selected]
    if kind == "detail" and "marked" in result:
        result.remove("marked")
        result.append("marked")
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
        if hasattr(view, "cell_tools"):
            view.cell_tools.schedule()
    def on_x_scroll(first: str, last: str) -> None:
        xbar.set(first, last)
        view.dashed_grid.schedule_scroll()
        if hasattr(view, "cell_tools"):
            view.cell_tools.schedule()
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
        columns = [item for item in VIEW_COLUMNS[kind] if kind != "voucher" or item[0] != "source"]
        self.vars = {key: tk.BooleanVar(value=key in selected) for key, _, _ in columns}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="勾选要显示的列，修改后立即生效。", foreground=theme.MUTED).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for index, (key, label, _) in enumerate(columns):
            ttk.Checkbutton(body, text=label, variable=self.vars[key], command=self._apply).grid(row=1 + index // 2, column=index % 2, sticky="w", padx=(0, 26), pady=4)
        row = 1 + (len(columns) + 1) // 2
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
    def __init__(self, parent: "App", *, ai=False):
        super().__init__(parent)
        self.withdraw()
        self.parent = parent
        self.title("设置")
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._finish)
        tabs = ttk.Notebook(self)
        self.tabs = tabs
        tabs.pack(fill='both', expand=True)
        pref_host = ttk.Frame(tabs)
        tabs.add(pref_host, text='偏好')
        viewport = ttk.Frame(pref_host)
        viewport.pack(fill='both', expand=True)
        canvas = tk.Canvas(viewport, width=610, height=min(640, max(420, self.winfo_screenheight() - 180)), highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(canvas, padding=16)
        content = canvas.create_window(0, 0, window=body, anchor='nw')
        body.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(content, width=event.width))
        self.bind('<MouseWheel>', lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units'))
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
        ttk.Separator(body).pack(fill='x', pady=12)
        ttk.Label(body, text='Excel 导入 · 新账套保存位置').pack(anchor='w')
        self.book_location_mode = tk.StringVar(value=parent.controller.preferences.get('book_location_mode', 'source'))
        ttk.Radiobutton(body, text='与原 Excel 放在同一目录（默认）', variable=self.book_location_mode,
            value='source', command=self._save_book_location).pack(anchor='w', pady=(6, 0))
        ttk.Radiobutton(body, text='统一保存到指定文件夹', variable=self.book_location_mode,
            value='fixed', command=self._save_book_location).pack(anchor='w')
        location = ttk.Frame(body)
        location.pack(fill='x', pady=4)
        self.book_folder_label = tk.StringVar(value=str(parent.controller.book_folder))
        ttk.Label(location, textvariable=self.book_folder_label, wraplength=410, style='Hint.TLabel').pack(side='left', fill='x', expand=True)
        ttk.Button(location, text='选择文件夹…', command=self._choose_book_folder).pack(side='right')
        ttk.Label(body, text='多文件导入时以第一个文件的目录为准；同名账套自动加序号。', style='Hint.TLabel').pack(anchor='w')
        shortcut_host = ttk.Frame(tabs, padding=16)
        tabs.add(shortcut_host, text='快捷键')
        ttk.Label(shortcut_host, text='凭证操作快捷键', style='Title.TLabel').pack(anchor='w')
        ttk.Label(shortcut_host, text='点击“录制”后按组合键；按 Esc 取消录制。\n适用于明细账、关注凭证和已打开的凭证。编辑文字时不触发。',
                  style='Hint.TLabel', wraplength=550).pack(anchor='w', pady=(6, 14))
        self.shortcut_vars = {}
        self.shortcut_buttons = {}
        for action, value in configured_shortcuts(parent.controller.preferences).items():
            row = ttk.Frame(shortcut_host)
            row.pack(fill='x', pady=6)
            ttk.Label(row, text=SHORTCUT_LABELS[action], width=19).pack(side='left')
            variable = tk.StringVar(self, value)
            self.shortcut_vars[action] = variable
            ttk.Entry(row, textvariable=variable, state='readonly', width=22).pack(side='left', padx=8)
            button = ttk.Button(row, text='录制', command=lambda key=action: self._record_shortcut(key))
            button.pack(side='left')
            self.shortcut_buttons[action] = button
        self.shortcut_status = tk.StringVar(self)
        ttk.Label(shortcut_host, textvariable=self.shortcut_status, style='Hint.TLabel', wraplength=550).pack(anchor='w', pady=8)
        ttk.Button(shortcut_host, text='恢复默认快捷键', command=self._reset_shortcuts).pack(anchor='w', pady=8)
        ai_host = ttk.Frame(tabs)
        self.ai_tab = ai_host
        tabs.add(ai_host, text='AI 服务')
        ai_canvas = tk.Canvas(ai_host, width=610, height=min(600, max(300, self.winfo_screenheight() - 230)), highlightthickness=0)
        ai_scrollbar = ttk.Scrollbar(ai_host, orient='vertical', command=ai_canvas.yview)
        ai_scrollbar.pack(side='right', fill='y')
        ai_canvas.pack(side='left', fill='both', expand=True)
        ai_canvas.configure(yscrollcommand=ai_scrollbar.set)
        ai_tab = ttk.Frame(ai_canvas, padding=16)
        ai_content = ai_canvas.create_window(0, 0, window=ai_tab, anchor='nw')
        ai_tab.bind('<Configure>', lambda _event: ai_canvas.configure(scrollregion=ai_canvas.bbox('all')))
        ai_canvas.bind('<Configure>', lambda event: ai_canvas.itemconfigure(ai_content, width=event.width))
        self.bind('<MouseWheel>', lambda event: (ai_canvas if tabs.select() == str(ai_host) else canvas).yview_scroll(-1 if event.delta > 0 else 1, 'units'))
        self.ai_step_heading = tk.StringVar(value='AI 服务与调用顺序')
        ttk.Label(ai_tab, textvariable=self.ai_step_heading, style='Title.TLabel').pack(anchor='w')
        ttk.Label(ai_tab, text=
                  "导入账簿：DeepSeek Flash 分析表头和列映射。\n凭证抽查：百炼（确认免费可用）→ Jev → DeepSeek Flash。\n未配置、额度不足或服务不可用时顺延；没有可用密钥则提示配置。",
                  wraplength=550, justify='left').pack(anchor='w', pady=6)
        self.provider = tk.StringVar(value=PROVIDER_LABELS["deepseek"])
        preference = ttk.Frame(ai_tab)
        preference.pack(fill='x', pady=(8, 5))
        ttk.Label(preference, text='凭证抽查优先模型').pack(side='left')
        self.review_preferred = tk.StringVar(value='百炼优先（默认）')
        ttk.Label(preference, text='百炼免费可用时优先，其次 Jev，最后 DeepSeek').pack(side='left', padx=8)
        provider_tabs = ttk.Notebook(ai_tab)
        provider_tabs.pack(fill='x', pady=5)
        jev_host, bailian_host, deepseek_host = (ttk.Frame(provider_tabs, padding=8) for _ in range(3))
        provider_tabs.add(bailian_host, text='阿里百炼决策模型')
        provider_tabs.add(jev_host, text='Jev（可选）')
        provider_tabs.add(deepseek_host, text='DeepSeek · 导入识别')
        self.deepseek_host, self.bailian_host = deepseek_host, bailian_host
        self.provider_tabs = provider_tabs
        ttk.Button(jev_host, text='打开 Jev 官网申请 API Key（可选）', command=lambda: webbrowser.open('https://console.typesafe.ai/')).pack(anchor='w')
        ttk.Label(jev_host, text="Jev API Key（仅用于凭证尽调筛查）").pack(anchor="w", pady=(7, 0))
        key_row = ttk.Frame(jev_host)
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
        ttk.Label(jev_host, textvariable=self.key_status, foreground=theme.MUTED).pack(anchor="w", pady=(4, 0))
        self._refresh_key_status()
        self._build_bailian_settings(bailian_host)
        ttk.Button(deepseek_host, text='打开 DeepSeek 开放平台申请 API Key', command=lambda: webbrowser.open('https://platform.deepseek.com/api_keys')).pack(anchor='w')
        ttk.Label(deepseek_host, text='用于导入识别，也用于凭证抽查兜底；API 调用费用以平台账户为准。', wraplength=510).pack(anchor='w', pady=4)
        ttk.Label(deepseek_host, text="DeepSeek API Key").pack(anchor="w", pady=(7, 0))
        deepseek_row = ttk.Frame(deepseek_host)
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
        ttk.Label(deepseek_host, textvariable=self.deepseek_status, foreground=theme.MUTED).pack(anchor="w", pady=(4, 0))
        self._refresh_deepseek_status()
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(ai_tab, text="显示明文（三个密钥）", variable=self.show_key, command=self._toggle_key_visible).pack(anchor="w", pady=(2, 0))
        jev_actions = ttk.Frame(ai_tab)
        jev_actions.pack(fill="x", pady=6)
        from .jev_ui import launch_connection_test
        ttk.Button(jev_actions, text="测试 Jev（虚构内容）", command=lambda: launch_connection_test(self, self.key_status)).pack(side="left")
        ttk.Button(jev_actions, text="AI审查设置", command=parent.open_review_rules, state="normal" if parent.store else "disabled").pack(side="left", padx=6)
        ttk.Label(ai_tab, text="AI 建议列使用 DeepSeek Flash。" + PRIVACY_NOTICE
            + "保存密钥后可直接建议列；也可取消 AI 改用手动核对。完整账簿的解析、预检和疑点处理均在本机完成。",
            foreground=theme.MUTED, wraplength=430, justify="left").pack(anchor="w", pady=(7, 0))
        parties_tab = ttk.Frame(tabs)
        tabs.add(parties_tab, text='尽调主体')
        self.parties_panel = RelatedPartiesPanel(parties_tab, parent)
        self.parties_panel.pack(fill='both', expand=True)
        ttk.Label(body, text="字体、滚动和表格线适用于所有窗口；凭证与首页偏好只适用于当前账套。", foreground=theme.MUTED).pack(anchor="w", pady=(14, 0))
        self.storage_toggle = ttk.Button(body, text="› 账套辅助文件说明", style="Quiet.TButton", command=self._toggle_storage_help)
        self.storage_toggle.pack(anchor="w", pady=(12, 0))
        self.storage_help = ttk.Label(body, text=(
            "账套打开时，同目录可能出现 .sqlite-wal 和 .sqlite-shm 两个辅助文件。\n"
            ".sqlite-wal：写入日志，暂存尚未合并到账套主文件的最新修改。\n"
            ".sqlite-shm：共享内存索引，帮助多个读取任务协调访问写入日志。\n"
            "它们由软件自动管理，正常关闭所有使用该账套的窗口后通常会自动清理。"
            "使用中请勿手动删除；复制或移动账套前，请先在软件内关闭该账套，"
            "避免遗漏仍保存在日志中的修改。"), style="Hint.TLabel", wraplength=550, justify="left")
        footer = ttk.Frame(self, padding=(16, 8))
        footer.pack(fill='x')
        self.finish_button = ttk.Button(footer, text="保存并完成", command=self._finish, style='Primary.TButton')
        self.finish_button.pack(side='right')
        if ai:
            tabs.select(ai_host)
            provider_tabs.select(deepseek_host)
        self.update_idletasks()
        center_window(self, parent, self.winfo_reqwidth(), self.winfo_reqheight())
        self.deiconify()

    def _record_shortcut(self, action):
        self._stop_shortcut_recording()
        self.shortcut_status.set('请按下“' + SHORTCUT_LABELS[action] + '”的新组合键…')
        self.shortcut_buttons[action].configure(text='录制中…')
        self.shortcut_buttons[action].focus_set()
        def capture(event):
            if event.keysym == 'Escape':
                self._stop_shortcut_recording()
                self.shortcut_status.set('已取消录制')
                return 'break'
            pressed = event_shortcut(event, self.tk.call('tk', 'windowingsystem') == 'aqua')
            if not pressed or '+' not in pressed:
                return 'break'
            try:
                value = validate_shortcuts({key: pressed if key == action else var.get()
                                            for key, var in self.shortcut_vars.items()})
            except ValueError as exc:
                self.shortcut_status.set(str(exc))
                return 'break'
            self.shortcut_vars[action].set(value[action])
            self._stop_shortcut_recording()
            self.shortcut_status.set('点击“保存并完成”后生效。')
            return 'break'
        self._shortcut_recording = self.bind('<KeyPress>', capture, add='+')

    def _stop_shortcut_recording(self):
        binding = getattr(self, '_shortcut_recording', None)
        if binding:
            self.unbind('<KeyPress>', binding)
            self._shortcut_recording = None
        for button in self.shortcut_buttons.values():
            button.configure(text='录制')

    def _reset_shortcuts(self):
        self._stop_shortcut_recording()
        for key, value in DEFAULT_SHORTCUTS.items():
            self.shortcut_vars[key].set(value)
        self.shortcut_status.set('已恢复默认，点击“保存并完成”后生效。')

    def _save_book_location(self):
        self.parent.controller.preferences['book_location_mode'] = self.book_location_mode.get()
        self.parent.controller._save_preferences()

    def _choose_book_folder(self):
        self.parent.choose_book_folder()
        self.book_location_mode.set(self.parent.controller.preferences.get('book_location_mode', 'source'))
        self.book_folder_label.set(str(self.parent.controller.book_folder))

    def _toggle_storage_help(self):
        expanded = bool(self.storage_help.winfo_manager())
        self.storage_help.pack_forget() if expanded else self.storage_help.pack(anchor="w", pady=(5, 0))
        self.storage_toggle.configure(text=("›" if expanded else "⌄") + " 账套辅助文件说明")

    def _build_bailian_settings(self, host):
        ttk.Button(host, text='打开百炼决策模型申请与使用说明', command=lambda: webbrowser.open('https://help.aliyun.com/zh/model-studio/decision-model-api')).pack(anchor='w')
        links = ttk.Frame(host)
        links.pack(fill='x', pady=3)
        ttk.Button(links, text='百炼控制台 · 申请密钥', command=lambda: webbrowser.open('https://bailian.console.aliyun.com/')).pack(side='left')
        ttk.Button(links, text='查看限时免费政策', command=lambda: webbrowser.open('https://help.aliyun.com/zh/model-studio/decision-model-preview/')).pack(side='left', padx=5)
        ttk.Button(host, text='专属地址与 API Key 配置说明', command=lambda: webbrowser.open('https://docs.bailian.console.aliyun.com/zh/model-studio/regions#h2_migrate_domain')).pack(anchor='w', pady=3)
        ttk.Label(host, text='百炼 API Key（decision-model-preview）').pack(anchor='w')
        self.bailian_key = tk.StringVar(value=bailian_get_api_key())
        row = ttk.Frame(host)
        row.pack(fill='x', pady=5)
        self.bailian_entry = ttk.Entry(row, textvariable=self.bailian_key, show='•', width=29)
        self.bailian_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='保存密钥', command=self._save_bailian_key).pack(side='left', padx=5)
        ttk.Button(row, text='删除密钥', command=self._delete_bailian_key).pack(side='left')
        self.bailian_status = tk.StringVar()
        ttk.Label(host, textvariable=self.bailian_status, foreground='#555').pack(anchor='w')
        self._refresh_bailian_status()
        ttk.Label(host, text='决策接口地址（默认北京试用域名；可改为控制台的业务空间专属地址）').pack(anchor='w', pady=(6, 0))
        self.bailian_endpoint = tk.StringVar(value=self.parent.controller.preferences.get('bailian_endpoint', BAILIAN_ENDPOINT))
        ttk.Entry(host, textvariable=self.bailian_endpoint).pack(fill='x', pady=4)
        ttk.Button(host, text='测试百炼连接（虚构内容）', command=self._test_bailian_connection).pack(anchor='w', pady=4)
        self.bailian_free_confirmed = tk.BooleanVar(value=self.parent.controller.preferences.get('bailian_free_confirmed') is True)
        ttk.Checkbutton(host, text='我已确认该模型当前限时免费，启用百炼', variable=self.bailian_free_confirmed).pack(anchor='w', pady=4)
        ttk.Label(host, text='官方目前标注“限时免费”，开通资格和期限以平台为准。程序无法查询计费状态；未勾选时跳过百炼。免费条件变化后请取消勾选；控制台如提供“免费额度用完即停”，建议开启。', foreground='#555', wraplength=510).pack(anchor='w')

    def _test_bailian_connection(self):
        if not self.bailian_free_confirmed.get():
            self.bailian_status.set('请先核实官方免费政策，再勾选启用百炼。')
            return
        from .jev_ui import launch_connection_test
        launch_connection_test(self, self.bailian_status, provider='bailian',
                               api_key=self.bailian_key.get(), endpoint=self.bailian_endpoint.get())

    def _refresh_bailian_status(self):
        self.bailian_status.set(('密钥仅保留在本次运行内存' if bailian_memory_only() else '密钥已保存到系统安全凭据库') if bailian_get_api_key() else '尚未保存百炼密钥')

    def _save_bailian_key(self):
        try:
            bailian_save_api_key(self.bailian_key.get())
        except Exception as exc:
            messagebox.showerror('保存失败', str(exc), parent=self)
            return False
        self.bailian_key.set(bailian_get_api_key())
        self._refresh_bailian_status()
        return True

    def _delete_bailian_key(self):
        try:
            bailian_delete_api_key()
        except Exception as exc:
            messagebox.showerror('删除失败', str(exc), parent=self)
            return
        self.bailian_key.set('')
        self._refresh_bailian_status()

    def _save_mode(self) -> None:
        if self.parent.store:
            self.parent.store.save_setting("voucher_open_mode", self.mode.get())

    def _save_provider(self) -> None:
        self.parent.controller.set_ai_provider("deepseek")

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
        self.bailian_entry.configure(show=show)

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
        try:
            shortcuts = validate_shortcuts({key: var.get() for key, var in self.shortcut_vars.items()})
        except ValueError as exc:
            messagebox.showerror('快捷键无效', str(exc), parent=self)
            return
        self._stop_shortcut_recording()
        try:
            endpoint = validate_bailian_endpoint(self.bailian_endpoint.get())
        except ValueError as exc:
            messagebox.showerror('百炼地址无效', str(exc), parent=self)
            return
        if self.api_key.get() and self.api_key.get() != get_api_key() and not self._save_api_key():
            return
        if self.deepseek_key.get() and self.deepseek_key.get() != deepseek_get_api_key() and not self._save_deepseek_key():
            return
        if self.bailian_key.get() and self.bailian_key.get() != bailian_get_api_key() and not self._save_bailian_key():
            return
        self.parent.controller.preferences.update(voucher_shortcuts=shortcuts, bailian_endpoint=endpoint,
            review_preferred_provider='bailian', bailian_free_confirmed=self.bailian_free_confirmed.get())
        self.parent.controller._save_preferences()
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

    def __init__(self, parent: "RelatedPartiesPanel"):
        super().__init__(parent)
        self.dialog = parent
        self.title("批量导入尽调主体")
        self.geometry("600x500")
        self.minsize(500, 400)
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, foreground=theme.MUTED, wraplength=540, text="每行一个主体，可用“｜”补充身份，例如“张三｜股东,董事”；空白行与重复项自动忽略。"
            "也可从 Excel/CSV/TXT 导入：第一列名称，第二列身份（如“股东、董事”，识别不了的自动留空），首行表头自动跳过。"
            "与现有名单重复或无效的条目导入时跳过。").pack(anchor="w")
        ttk.Label(body, foreground=theme.MUTED, wraplength=540, text=f"可选身份：{'、'.join(PARTY_ROLES)}").pack(anchor="w", pady=(4, 0))
        self.editor = tk.Text(body, height=11, wrap="word", undo=True)
        self.editor.pack(fill="both", expand=True, pady=(8, 6))
        self.status = tk.StringVar()
        ttk.Label(body, textvariable=self.status, foreground=theme.MUTED).pack(anchor="w")
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


class RelatedPartiesPanel(ttk.Frame):
    """手动维护尽调主体：输入只保留名称与关系；身份／别名经批量导入设置。

    Frame 形态以便同时用于独立对话框与设置对话框标签页。"""

    def __init__(self, master, parent: "App"):
        super().__init__(master, padding=12)
        self.parent = parent
        ttk.Label(self, text="手动输入只需名称与关系：“与目标公司关系”仅保存在本地账套，不发送给 AI。"
            "本地对往来单位、辅助核算作精确匹配，命中作为程序特征随凭证发送；AI 审查时名称、别名与身份（不含备注、关系）会随凭证发送到 Jev，用于识别关联资金往来，不构成法律关系认定。",
            foreground=theme.MUTED, wraplength=620, justify="left").pack(anchor="w")
        self.view = ttk.Treeview(self, columns=("name", "relation", "roles"), show="headings", height=9)
        for key, title, width in (("name", "名称", 220), ("relation", "与目标公司关系", 220), ("roles", "身份", 150)):
            self.view.heading(key, text=title)
            self.view.column(key, width=width)
        self.view.pack(fill="both", expand=True, pady=(8, 10))
        self.view.bind("<<TreeviewSelect>>", self._select)
        form = ttk.Frame(self)
        form.pack(fill="x")
        ttk.Label(form, text="名称").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="与目标公司关系").grid(row=1, column=0, sticky="w")
        self.relation_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.relation_var).grid(row=1, column=1, sticky="ew", pady=3)
        form.columnconfigure(1, weight=1)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="新增", command=self._clear).pack(side="left")
        ttk.Button(buttons, text="批量导入", command=lambda: RelatedPartyImportDialog(self)).pack(side="left", padx=6)
        ttk.Button(buttons, text="删除所选", command=self._delete, style="Danger.TButton").pack(side="left")
        ttk.Button(buttons, text="保存", command=self._save, style="Accent.TButton").pack(side="right")
        self._refresh()

    def _refresh(self) -> None:
        self.view.delete(*self.view.get_children())
        if not self.parent.store:
            return
        for row in self.parent.store.related_parties():
            roles = "、".join(json.loads(row["roles_json"])) if "roles_json" in row.keys() else ""
            relation = row["relation"].replace("\n", " ")[:60] if "relation" in row.keys() else ""
            self.view.insert("", "end", iid=str(row["id"]), values=(row["name"], relation, roles))
        self.parent.refresh_details()

    def _select(self, _event: object) -> None:
        selected = self.view.selection()
        if not selected:
            return
        row = next((item for item in self.parent.store.related_parties() if item["id"] == int(selected[0])), None)
        if row is None:
            return
        self.name_var.set(row["name"])
        self.relation_var.set(row["relation"] if "relation" in row.keys() else "")

    def _clear(self) -> None:
        selected = self.view.selection()
        if selected:
            self.view.selection_remove(*selected)
        self.name_var.set("")
        self.relation_var.set("")

    def _save(self) -> None:
        selected = self.view.selection()
        party_id = int(selected[0]) if selected else None
        # 只改名称与关系；身份、别名、备注保留原值（身份与别名可经批量导入维护）。
        roles: list[str] = []
        aliases: list[str] = []
        note = ""
        if party_id is not None:
            row = next((item for item in self.parent.store.related_parties() if item["id"] == party_id), None)
            if row is not None:
                roles = json.loads(row["roles_json"]) if "roles_json" in row.keys() else []
                aliases = json.loads(row["aliases_json"])
                note = row["note"]
        try:
            saved_id = self.parent.store.save_related_party(self.name_var.get(), aliases, note, party_id, roles,
                self.relation_var.get())
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


class RelatedPartiesDialog(tk.Toplevel):
    """独立窗口形态的尽调主体名单（侧栏入口保留）。"""

    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.parent = parent
        self.title("当前项目 · 尽调主体名单")
        self.geometry("680x520")
        self.transient(parent)
        self.panel = RelatedPartiesPanel(self, parent)
        self.panel.pack(fill="both", expand=True)
        ttk.Button(self.panel, text="完成", command=self.destroy).pack(side="right", pady=(8, 0))
        self.grab_set()


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
        ttk.Label(body, text="仅比较您确认的同一期间和来源；差异不会修改或阻止查看账簿。", foreground=theme.MUTED).pack(anchor="w")
        period_row = ttk.Frame(body)
        period_row.pack(fill="x", pady=(10, 5))
        ttk.Label(period_row, text="会计年度").pack(side="left")
        ttk.Entry(period_row, textvariable=self.year_var, width=8).pack(side="left", padx=(5, 18))
        ttk.Label(period_row, text="会计期间").pack(side="left")
        ttk.Entry(period_row, textvariable=self.period_var, width=5).pack(side="left", padx=5)
        ttk.Label(period_row, text="利润表金额列也按此期间比较；累计列请先确认对应口径。", foreground=theme.MUTED).pack(side="left", padx=16)
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


class PrecheckDetailWindow(tk.Toplevel):
    """导入后随时查看某张表保存的预检报告：结论、统计与疑点明细（只读）。"""

    LEVEL_LABELS = {SEVERITY_CRITICAL: "关键", SEVERITY_WARNING: "提醒", "info": "说明"}

    def __init__(self, parent, record: sqlite3.Row | None, source: sqlite3.Row):
        super().__init__(parent)
        self.title(f"导入预检 · {source['filename']} / {source['sheet']}")
        self.geometry("880x520")
        self.minsize(660, 400)
        self.transient(parent.winfo_toplevel())
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        if record is None:
            ttk.Label(body, text="这条导入记录来自旧版导入，没有保存预检报告；它保持原样，不追认已通过新校验。",
                foreground=theme.MUTED, wraplength=760).pack(anchor="w")
            ttk.Button(body, text="关闭", command=self.destroy).pack(anchor="e", pady=10)
            self.grab_set()
            return
        stats = PrecheckStats.from_json(json.loads(record["stats_json"]))
        try:
            issues = json.loads(record["issues_json"]) or []
        except (TypeError, ValueError):
            issues = []
        header = (f"预检结论：{record['verdict']}\n"
                  f"表头第 {record['header_row']} 行 · 拟导入 {stats.imported_rows} 行 · 待处理 {stats.pending_rows} 行 · "
                  f"排除 {stats.excluded_total} 行（{stats.exclusion_line() or '无'}）")
        if source["kind"] == "entry":
            header += f"\n借方合计 {format_money(stats.debit_cents, comma=True)} · 贷方合计 {format_money(stats.credit_cents, comma=True)}"
        header += f"\n疑点 {len(issues)} 条（关键 {sum(i.get('severity') == SEVERITY_CRITICAL for i in issues)} · 提醒 {sum(i.get('severity') == SEVERITY_WARNING for i in issues)}）"
        ttk.Label(body, text=header, foreground="#31435B", justify="left", wraplength=800).pack(anchor="w")
        self.view = tree(body, [("level", "级别", 55), ("row", "行", 55), ("reason", "问题原因", 330), ("interp", "当前解释", 150), ("action", "说明", 220)])
        for issue in issues:
            self.view.insert("", "end", values=(self.LEVEL_LABELS.get(issue.get("severity"), issue.get("severity", "")),
                issue.get("row_no") or "", issue.get("reason", ""), issue.get("interpretation", ""), issue.get("action", "")))
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, text="预检报告在导入时保存；此处只读，修改列设置需重新导入该表。", foreground=theme.MUTED).pack(side="left")
        ttk.Button(footer, text="关闭", command=self.destroy).pack(side="right")
        self.grab_set()


class ImportDialog(tk.Toplevel):
    def __init__(self, parent: "App", path: Path, *, spec=None, sheet_name=None, on_save=None):
        super().__init__(parent)
        self.parent = parent
        self.path = path
        self.on_save = on_save
        self.tasks = BackgroundTasks(self)
        self.large_file = requires_background([path])
        self.stop_requested = Event()
        self.working = False
        self.sheet_names = [sheet_name] if sheet_name else ([] if self.large_file else sheet_names(path))
        self.title("导入资料 · 核对每列含义")
        width = min(960, max(600, self.winfo_screenwidth() - 80))
        height = min(780, max(500, self.winfo_screenheight() - 150))
        self.geometry(f"{width}x{height}")
        self.transient(parent)
        self.grab_set()
        self.sheet = tk.StringVar(value=sheet_name or (self.sheet_names[0] if self.sheet_names else ""))
        self.kind = tk.StringVar(value="明细账／序时账")
        self.header = tk.StringVar(value="1")
        self.columns: list[str] = []
        self.vars: dict[str, tk.StringVar] = {}
        self._ai_review = None
        self._explicit_header = False
        self._layout()
        if spec is not None:
            self.kind.set(BatchImportDialog.LABELS[spec.kind])
            self.header.set(str(spec.header_row))
            self.refresh_preview(manual=True, mapping=spec.mapping)
        elif self.sheet.get():
            if self.large_file:
                self.refresh_preview(manual=on_save is not None, auto_kind=True)
            else:
                self._auto_kind_for_sheet()
                self.refresh_preview(manual=on_save is not None)
        else:
            self.import_button.configure(state="disabled")
            self.tasks.submit('sheets', lambda cancelled, report: sheet_names(path), self._sheets_ready,
                lambda exc: self.import_note.configure(text=f"读取失败：{exc}"))
        self.protocol("WM_DELETE_WINDOW", self._close_import)
        self.bind("<Destroy>", self._destroy_import, add="+")

    def _sheets_ready(self, names):
        self.sheet_names = names
        self.sheet_combo.configure(values=names)
        if names:
            self.sheet.set(names[0])
            self.refresh_preview(auto_kind=True)

    def _close_import(self):
        if self.working:
            self.stop_requested.set()
            self.import_note.configure(text="正在停止；当前未完成的工作表不会写入。")
        else:
            self.destroy()

    def _destroy_import(self, event):
        if event.widget is self:
            self.stop_requested.set()
            if getattr(self.parent, 'import_owner', None) is self:
                self.parent.import_busy = False
                self.parent.import_owner = None

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
        sheet = ttk.Combobox(row, textvariable=self.sheet, values=self.sheet_names, state="disabled" if self.on_save else "readonly", width=25)
        sheet.pack(side="left", padx=(6, 18))
        self.sheet_combo = sheet
        sheet.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview(auto_kind=True))
        ttk.Label(row, text="表类型").pack(side="left")
        kind = ttk.Combobox(row, textvariable=self.kind, values=("明细账／序时账", "科目余额表", "资产负债表", "利润表"), state="readonly", width=16)
        kind.pack(side="left", padx=(6, 18))
        kind.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        header_actions = ttk.Frame(top)
        header_actions.pack(fill="x", pady=(8, 0))
        ttk.Label(header_actions, text="表头行号").pack(side="left")
        ttk.Entry(header_actions, textvariable=self.header, width=5).pack(side="left", padx=6)
        ttk.Button(header_actions, text="按行号读取表头", command=self._read_header).pack(side="left", padx=5)
        self.ai_button = ttk.Button(header_actions, text="AI 建议列（可选）", command=self._offer_ai)
        self.ai_button.pack(side="right")
        ttk.Button(header_actions, text="复制给我的 Agent", command=lambda: copy_agent_task(self, [(self.path, self.sheet.get())])).pack(side="right", padx=5)
        ttk.Label(top, text="先看原表，再确认每个字段对应的列。〔空〕与原表的 0 分开显示。", foreground=theme.MUTED).pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(self, height=5, wrap="none", state="disabled", font="TkFixedFont")
        self.preview.pack(fill="x", padx=12, pady=8)
        bottom = ttk.Frame(self, padding=12)
        bottom.pack(side="bottom", fill="x")
        self.import_note = ttk.Label(bottom, text="账簿空白/小计行会跳过；请确认借贷列不是余额列。", foreground="#995300", wraplength=460)
        self.import_note.pack(side="left")
        ttk.Button(bottom, text="取消", command=self._close_import).pack(side="right", padx=4)
        self.import_button = ttk.Button(bottom, text="保存列设置" if self.on_save else "确认并导入", command=self.import_now)
        self.import_button.pack(side="right", padx=4)
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

    def _read_header(self):
        self._explicit_header = True
        self.refresh_preview(manual=True)

    def _editor_signature(self):
        return (self.sheet.get(), self.kind.get(), self.header.get(),
                tuple((field, var.get()) for field, var in self.vars.items()))

    def _offer_ai(self) -> None:
        if self.tasks.busy('review_ai') or self.working:
            return
        label, suggest, get_key = ai_tools(self.parent.controller)
        api_key = get_key()
        if not api_key:
            self.grab_release()
            settings = SettingsDialog(self.parent)
            self.wait_window(settings)
            if not self.winfo_exists():
                return
            self.grab_set()
            api_key = get_key()
            if not api_key:
                self.import_note.configure(text="尚未设置 AI；可手动设置，或复制提示词给 Agent。")
                return
        try:
            header_row = int(self.header.get()) if self._explicit_header else None
        except ValueError:
            self.import_note.configure(text="表头行号必须为正整数")
            return
        signature = self._editor_signature()
        path, sheet, kind = self.path, self.sheet.get(), self.kind_code()
        self.ai_button.configure(state="disabled", text="正在审查…")
        self.import_button.configure(state="disabled")
        self.import_note.configure(text=f"{label} 正在分析前 20 行与合并单元格，并在本机整表预检…")

        def restore():
            self.ai_button.configure(state="normal", text="AI 建议列（可选）")
            self.import_button.configure(state="normal")

        def done(review):
            restore()
            if signature != self._editor_signature():
                self.import_note.configure(text="列设置已改变，未应用刚才的 AI 建议；可重新审查。")
                return
            self._ai_review = review
            self.kind.set(BatchImportDialog.LABELS[review.spec.kind])
            self.header.set(str(review.spec.header_row))
            self.refresh_preview(manual=True, mapping=review.spec.mapping)

        def failed(exc):
            restore()
            self.import_note.configure(text="审查未完成：" + redact_error(exc, api_key) + "；可复制提示词给 Agent。")

        self.tasks.submit('review_ai', lambda stop, report: review_sheet(path, sheet, stop, report,
            suggest=suggest, api_key=api_key, header_row=header_row,
            kind=kind if header_row is not None else None), done, failed)

    def refresh_preview(self, manual: bool = False, *, auto_kind=False, mapping=None) -> None:
        if self.working:
            return
        self.tasks.cancel('preview')
        path, sheet, kind = self.path, self.sheet.get(), self.kind_code()
        try:
            row_no = int(self.header.get()) if manual else 1
        except ValueError:
            self.import_note.configure(text="表头行号必须为正整数")
            return
        templates = self.parent.store.template_candidates() if self.parent.store else None
        def query(cancelled, report):
            return preview_data(path, sheet, kind, manual, row_no, cancelled,
                auto_kind=auto_kind, templates=templates, mapping=mapping)
        if self.large_file:
            self.import_button.configure(state="disabled")
            self.import_note.configure(text="正在本机读取表头和预览…")
            self.tasks.submit('preview', query, self._render_preview,
                lambda exc: self.import_note.configure(text=f"读取失败，可重新读取：{exc}"))
        else:
            try:
                self._render_preview(query(Event(), lambda _: None))
            except Exception as exc:
                messagebox.showerror("读取失败", str(exc), parent=self)

    def _render_preview(self, result):
        row_no, headers, guess, preview, kind = result
        self.kind.set(BatchImportDialog.LABELS[kind])
        self.header.set(str(row_no))
        self.columns = [f"{i+1}: {name or '(空列)'}" for i, name in enumerate(headers)]
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", preview)
        self.preview.configure(state="disabled")
        self.import_button.configure(state="normal")
        for child in self.map_frame.winfo_children():
            child.destroy()
        self.vars.clear()
        kind_code = kind
        if kind_code in REPORT_KINDS:
            fields = ("left_item", "left_amount_1", "left_amount_2", "left_amount_3", "right_item", "right_amount_1", "right_amount_2", "right_amount_3") if kind_code == "balance_sheet" else ("left_item", "left_amount_1", "left_amount_2", "left_amount_3")
            self.import_note.configure(text="报表保留原行序、标题和小计；空白金额与零值分开保存，不做勾稽核对。")
        else:
            fields = ("date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_code", "account_name", "summary", "debit", "credit", "amount", "direction", "counterparty", "auxiliary", "currency", "original_amount", "maker") if kind_code == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "opening_direction", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "closing_balance", "closing_direction", "direction")
            self.import_note.configure(text="空白/小计行会跳过；金额单列时须同时选借贷方向列。")
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
        review = self._ai_review
        if review and review.spec.header_row == row_no and review.spec.mapping == guess:
            note = review.problem or review.report.summary_line()
            if review.notices:
                note += "；" + "；".join(review.notices)
            self.import_note.configure(text=note)

    def kind_code(self) -> str:
        if self.kind.get() == "明细账／序时账":
            return "entry"
        if self.kind.get() == "科目余额表":
            return "balance"
        return "balance_sheet" if self.kind.get() == "资产负债表" else "income_statement"

    def _set_import_working(self, working):
        self.working = working
        self.import_button.configure(state="disabled" if working else "normal")
        self.ai_button.configure(state="disabled" if working else "normal")
        if working:
            self.stop_requested.clear()
            self.parent.import_busy = True
            self.parent.import_owner = self
            self.parent.busy_start("正在导入，请稍候…")
        elif getattr(self.parent, 'import_owner', None) is self:
            self.parent.import_busy = False
            self.parent.import_owner = None
            self.parent.busy_stop()

    def _import_failed(self, exc):
        stopped = self.stop_requested.is_set() or isinstance(exc, Cancelled)
        self._set_import_working(False)
        self.import_note.configure(text="已停止；本工作表未写入，可重试。" if stopped else f"没有写入本次数据，可重试：{exc}")

    def _single_prepared(self, outcome):
        self._set_import_working(False)
        prepared, held, statuses, updated = outcome
        if not prepared:
            self.import_note.configure(text="未导入：" + statuses.get(0, "请检查列设置"))
            return
        spec, result, reasons = prepared[0]
        self._import_prechecked([(spec, result)], auto_excluded=reasons)

    def import_now(self) -> None:
        if self.working or self.tasks.busy('preview') or self.tasks.busy('review_ai'):
            return
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
        if self.large_file:
            self._set_import_working(True)
            self.import_note.configure(text="正在本机整表预检…")
            stop = self.stop_requested
            self.tasks.submit('precheck', lambda cancelled, report: prepare_sheets([(0, spec)], stop, report),
                self._single_prepared, self._import_failed,
                lambda value: self.import_note.configure(text=value))
            return
        try:
            result = precheck(spec)
        except Exception as exc:
            messagebox.showerror("预检失败", f"没有写入本次数据。\n{exc}", parent=self)
            return
        # 疑点行仅由本地规则处理；无法安全处理时交回人工。
        if result.pending_rows:
            self._auto_resolve(spec, result)
            return
        if result.blocked:
            criticals = [issue.reason for issue in result.issues if issue.severity == SEVERITY_CRITICAL][:3]
            self.import_note.configure(text="未导入：" + "；".join(criticals or ["没有识别到可导入的数据行"])
                + "。请调整表头行或列设置后重试。")
            return
        self._import_prechecked([(spec, result)])

    def _auto_resolve(self, spec, result) -> None:
        # Import never sends questionable business rows to a cloud service.
        self._apply_resolution(spec, result, None)

    def _apply_resolution(self, spec, result, labels) -> None:
        from .row_triage import pending_reasons, resolve_pending

        outcome = resolve_pending(spec, pending_reasons(result), result.stats.imported_rows, labels)
        if not outcome.resolved:
            shown = "；".join(f"第 {row} 行 {reason}" for row, reason in sorted(outcome.held.items())[:3])
            more = "…" if len(outcome.held) > 3 else ""
            self.import_note.configure(text=f"未导入：{len(outcome.held)} 行疑点被判断为真实数据或结构变化，不能自动排除。{shown}{more}"
                + "请调整列设置或修正原表后重试。")
            return
        if outcome.excluded:
            spec = spec.with_extra_exclusions(tuple(outcome.excluded))
            try:
                result = precheck(spec)
            except Exception as exc:
                messagebox.showerror("预检失败", f"没有写入本次数据。\n{exc}", parent=self)
                return
            if result.blocked:
                self.import_note.configure(text="未导入：排除疑点行后仍没有可导入的数据行，请调整列设置后重试。")
                return
        self._import_prechecked([(spec, result)], auto_excluded=outcome.excluded)

    def _import_prechecked(self, work: list, auto_excluded: dict | None = None) -> None:
        if not work:
            return
        auto_excluded = auto_excluded or {}
        spec, result = work[0]
        if self.large_file:
            old_sources = self.parent.store.sources()
            duplicate = any(source["kind"] == spec.kind and source["sheet"] == spec.sheet and
                (source["sha256"] == result.fingerprint or source["original_path"] == str(spec.path.resolve())) for source in old_sources)
            if duplicate and not messagebox.askyesno("重新导入", "同一来源工作表已有导入记录。是否替换？原始文件不会修改。", parent=self):
                return
            self._set_import_working(True)
            path, stop = self.parent.store.path, self.stop_requested
            def write(cancelled, report):
                store = LedgerStore.connect_existing(path, readonly=False, cancelled=stop)
                try:
                    return store.import_sheet(spec, replace=duplicate, exclusion_reasons=auto_excluded,
                        expected_fingerprint=result.fingerprint, cancelled=stop,
                        progress=lambda count: report(f"正在写入 {count:,} 行…"))
                finally:
                    store.close()
            def finish(count):
                self._set_import_working(False)
                parent = self.parent
                self.destroy()
                parent.refresh_all()
                messagebox.showinfo("导入完成", f"成功导入 {count:,} 行。核对提醒会自动更新。", parent=parent)
            self.tasks.submit('write', write, finish, self._import_failed,
                lambda value: self.import_note.configure(text=value))
            return
        try:
            if file_hash(spec.path) != result.fingerprint:
                if not messagebox.askyesno("文件已修改", "预检之后文件内容发生了变化，建议重新预检。是否重新预检后再导入？", parent=self):
                    return
                self.import_now()
                return
            count = self.parent.store.import_sheet(spec, exclusion_reasons=auto_excluded)
        except sqlite3.IntegrityError:
            if not messagebox.askyesno("重新导入", "同一来源工作表已有导入记录。是否用当前列映射替换旧导入记录？原始 Excel 不会修改。", parent=self):
                return
            try:
                count = self.parent.store.import_sheet(spec, replace=True, exclusion_reasons=auto_excluded)
            except Exception as exc:
                messagebox.showerror("替换失败", f"旧导入记录已保留。\n{exc}", parent=self)
                return
        except Exception as exc:
            messagebox.showerror("导入失败", f"没有写入本次数据。\n{exc}", parent=self)
            return
        excluded_note = ""
        if result.stats.excluded_total:
            excluded_note = f"预检排除 {result.stats.excluded_total} 行（{result.stats.exclusion_line()}）。"
        if auto_excluded:
            excluded_note = f"已自动排除 {len(auto_excluded)} 行疑点行（理由见导入记录的预检详情）。" + excluded_note
        self.destroy()
        self.parent.refresh_all()
        messagebox.showinfo("导入完成", f"成功导入 {count} 行。{excluded_note}核对提醒会自动更新，可从“查看”菜单打开。", parent=self.parent)


class BatchImportDialog(tk.Toplevel):
    LABELS = {"entry": "明细账／序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}

    def __init__(self, parent: "App", paths: list[Path]):
        super().__init__(parent)
        self.parent = parent
        self.items, self.skipped, self.item_status = [], [], {}
        self.selected, self.manual, self.completed = set(), set(), set()
        self.reviews, self.review_errors = {}, {}
        self.local_mode = False
        self.ai_running = self.working = False
        self.ai_cancel, self.stop_requested = Event(), Event()
        self.tasks = BackgroundTasks(self)
        self._editor = None
        self._needs_key = False
        templates = parent.store.template_candidates() if parent.store else None
        background = requires_background(paths)
        if not background:
            self.items, self.skipped, self.item_status = inventory(paths, templates, self.stop_requested, lambda _: None)
            if not self.items and not self.skipped:
                self.destroy()
                return
        self.title("导入表格 · 自动审查")
        width = min(1020, self.winfo_screenwidth() - 80)
        height = min(760, self.winfo_screenheight() - 120)
        self.geometry(f"{width}x{height}")
        self.minsize(min(700, width), min(560, height))
        self.transient(parent)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="自动审查表头并在本机预检；查看建议后，点击一次“确认导入”。", wraplength=600).pack(anchor="w")
        self.ai_hint = ttk.Label(body, text="DeepSeek Flash 默认分析前 20 行及合并单元格，可能包含真实账目；整表预检留在本机。", foreground="#777", wraplength=600)
        self.ai_hint.pack(fill="x", pady=(4, 8))
        self.ai_progress = ttk.Progressbar(body, mode="determinate")
        self.ai_status = tk.StringVar(value="正在准备自动审查…")
        status_label = ttk.Label(body, textvariable=self.ai_status, foreground=theme.MUTED, wraplength=650)
        status_label.pack(fill="x", pady=(0, 6))
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width - 8)))
        footer = ttk.Frame(body)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        primary = ttk.Frame(footer)
        primary.pack(fill="x")
        self.cancel_button = ttk.Button(primary, text="关闭", command=self._close)
        self.cancel_button.pack(side="right", padx=(6, 0))
        self.import_button = ttk.Button(primary, text="确认导入", style="Accent.TButton", command=self._import_selected, state="disabled")
        self.import_button.pack(side="right")
        self.agent_button = ttk.Button(primary, text="复制提示词给我的 Agent", command=self._copy_agent)
        self.agent_button.pack(side="left")
        secondary = ttk.Frame(footer)
        secondary.pack(fill="x", pady=(6, 0))
        self.edit_button = ttk.Menubutton(secondary, text="手动设置…")
        menu = tk.Menu(self.edit_button, tearoff=False)
        menu.add_command(label="调整当前工作表的列…", command=self._edit_current)
        menu.add_command(label="仅用本地规则和手动设置", command=self._use_local_mode)
        menu.add_command(label="恢复 AI 自动审查", command=self._use_ai_mode)
        self.edit_button.configure(menu=menu)
        self.edit_button.pack(side="left")
        self.retry_button = ttk.Button(secondary, text="重新审查", style="Quiet.TButton", command=self._retry_review)
        self.retry_button.pack(side="left", padx=12)
        self.settings_button = ttk.Button(secondary, text="设置 AI", style="Quiet.TButton", command=self._configure_ai)
        self.settings_button.pack(side="left")
        detail_holder = ttk.LabelFrame(body, text="当前工作表的建议与预检", padding=6)
        detail_holder.pack(side="bottom", fill="both", pady=(8, 0))
        self.detail = tk.Text(detail_holder, height=7, width=1, wrap="word", state="disabled", relief="flat")
        detail_scroll = ttk.Scrollbar(detail_holder, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(fill="both", expand=True)
        self.view = tree(body, [("selected", "导入", 50), ("file", "文件", 170),
            ("sheet", "工作表", 130), ("kind", "类型", 140), ("state", "审查结果", 260)])
        self.view.configure(height=5)
        self.selected = set(range(len(self.items)))
        for index in range(len(self.items)):
            self._render_item(index)
        self.view.bind("<Button-1>", self._toggle_item)
        self.view.bind("<Double-1>", self._edit_current)
        self.view.bind("<<TreeviewSelect>>", self._show_sheet_status)
        if self.items:
            self.view.selection_set('0')
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._destroy_import, add="+")
        if background:
            self._set_working(True, "正在本机读取工作表…")
            self.tasks.submit('inventory', lambda stop, report: inventory(paths, templates, stop, report),
                self._inventory_ready, self._work_failed, self.ai_status.set)
        else:
            self.after_idle(self._start_review)

    def _own_import(self, active):
        if active:
            self.parent.import_busy = True
            self.parent.import_owner = self
        elif getattr(self.parent, 'import_owner', None) is self:
            self.parent.import_busy = False
            self.parent.import_owner = None

    def _destroy_import(self, event):
        if event.widget is self:
            self.stop_requested.set()
            self.ai_cancel.set()
            self._own_import(False)

    def _set_working(self, working, text=""):
        self.working = working
        self._own_import(working or self.ai_running)
        if working:
            self.stop_requested.clear()
        self._update_controls()
        if text:
            self.ai_status.set(text)

    def _update_controls(self):
        busy = self.working or self.ai_running
        ready = bool(self.selected) and all(i in self.reviews and self.reviews[i].ready for i in self.selected)
        self.import_button.configure(state="normal" if ready and not busy and not self._editor else "disabled")
        self.agent_button.configure(state="normal" if self.selected and not self.working else "disabled")
        failed = any(i in self.review_errors or (i in self.reviews and not self.reviews[i].ready) for i in self.selected)
        self.agent_button.configure(style="Accent.TButton" if failed or self._needs_key else "TButton")
        self.edit_button.configure(state="disabled" if self.working else "normal")
        self.retry_button.configure(state="disabled" if busy or not self.selected or self._editor else "normal")
        self.settings_button.configure(state="disabled" if busy or self._editor else "normal")
        self.cancel_button.configure(text="停止" if self.working else "关闭", state="normal")

    def _inventory_ready(self, result):
        self.items, self.skipped, self.item_status = result
        self.selected = set(range(len(self.items)))
        for index in range(len(self.items)):
            self._render_item(index)
        self._set_working(False)
        if self.items:
            self.view.selection_set('0')
        elif not self.skipped:
            self.destroy()
            return
        self._start_review()

    def _work_failed(self, exc):
        stopped = isinstance(exc, Cancelled) or self.stop_requested.is_set()
        self._set_working(False, "已停止，已完成的工作表保留；可继续重试。" if stopped else f"处理失败：{exc}；可重试或交给 Agent。")

    def _cancel_review(self):
        self.tasks.cancel('review')
        self.ai_cancel.set()
        self.ai_running = False
        self.ai_progress.pack_forget()
        self._own_import(self.working)

    def _close(self):
        if not self.winfo_exists():
            return
        if self.working:
            self.stop_requested.set()
            for key in ('verify', 'inventory'):
                if self.tasks.busy(key):
                    self.tasks.cancel(key)
                    self._set_working(False, "已停止；没有写入本次数据，可关闭或重新操作。")
                    return
            self.ai_status.set("正在停止；当前未完成的工作表不会写入，已完成的保留。")
            self.cancel_button.configure(state="disabled")
            return
        self._cancel_review()
        self.destroy()

    def _use_local_mode(self):
        if self.working:
            return
        self._cancel_review()
        self.local_mode = True
        self.reviews.clear()
        self.review_errors.clear()
        self.ai_hint.configure(text="手动模式：仅用本地规则和手动设置；不会发送表头。")
        self._start_review()

    def _use_ai_mode(self):
        if self.working:
            return
        self._cancel_review()
        self.local_mode = False
        self.reviews = {i: review for i, review in self.reviews.items() if i in self.manual}
        self.review_errors.clear()
        self.ai_hint.configure(text="DeepSeek Flash 默认分析前 20 行及合并单元格，可能包含真实账目；整表预检留在本机。")
        self._start_review()

    def _configure_ai(self):
        if self.working or self.ai_running or self._editor:
            return
        self.grab_release()
        settings = SettingsDialog(self.parent)
        self.wait_window(settings)
        if self.winfo_exists():
            self.grab_set()
            self._retry_review()

    def _retry_review(self):
        if self.working or self.ai_running or self._editor:
            return
        failed = {i for i in self.selected if i in self.review_errors or
                  (i in self.reviews and not self.reviews[i].ready)}
        for index in failed or self.selected:
            self.reviews.pop(index, None)
            self.review_errors.pop(index, None)
            self.item_status.pop(index, None)
        self._start_review()

    def _start_review(self):
        if not self.winfo_exists() or self.working or self.ai_running or self._editor:
            return
        pending = [i for i in sorted(self.selected) if i not in self.reviews and i not in self.review_errors]
        _, suggest, get_key = ai_tools(self.parent.controller)
        api_key = ""
        self._needs_key = False
        if any(i not in self.manual for i in pending) and not self.local_mode:
            try:
                api_key = get_key()
            except Exception:
                api_key = ""
            if not api_key:
                self._needs_key = True
                for i in pending:
                    if i not in self.manual:
                        self.review_errors[i] = "尚未设置 AI；点击“设置 AI”，或复制提示词给我的 Agent。"
                pending = [i for i in pending if i in self.manual]
        if not pending:
            self._review_finished()
            return
        self.ai_running = True
        self.ai_cancel.clear()
        self._own_import(True)
        self.ai_progress.configure(maximum=len(pending), value=0)
        self.ai_progress.pack(fill="x", before=self.view.master, pady=(0, 5))
        self._update_controls()
        self._review_next(pending, api_key, suggest, 0)

    def _review_next(self, pending, api_key, suggest, done):
        if not pending:
            self.ai_running = False
            self._own_import(False)
            self.ai_progress.pack_forget()
            self._review_finished()
            return
        index, *rest = pending
        path, sheet, spec = self.items[index]
        local = self.local_mode or index in self.manual
        self.item_status[index] = "正在本机预检…" if local else "AI 正在审查表头…"
        self._render_item(index)
        self.ai_status.set(f"正在审查 {path.name}/{sheet}；已完成 {done} 张。")

        def work(stop, report):
            if local and spec is None:
                raise ValueError("本地规则无法识别，请从“手动设置…”调整当前表。")
            return review_sheet(path, sheet, stop, report, suggest=suggest, api_key=api_key,
                                manual_spec=spec if local else None)

        def finish(review=None, error=None):
            if review is not None:
                self.reviews[index] = review
                self.items[index] = (path, sheet, review.base_spec)
                self.item_status.pop(index, None)
            else:
                self.review_errors[index] = redact_error(error, api_key)
                self.item_status.pop(index, None)
            self._render_item(index)
            self._show_sheet_status()
            self.ai_progress.configure(value=done + 1)
            self._review_next(rest, api_key, suggest, done + 1)

        self.tasks.submit('review', work, lambda review: finish(review=review), lambda exc: finish(error=exc), self.ai_status.set)

    def _review_finished(self):
        for index in range(len(self.items)):
            self._render_item(index)
        ready = sum(i in self.reviews and self.reviews[i].ready for i in self.selected)
        if not self.selected:
            summary = "请勾选要导入的工作表。"
        elif ready == len(self.selected):
            count = sum(self.reviews[i].report.stats.imported_rows for i in self.selected)
            summary = f"所选 {ready} 张表全部通过，拟导入 {count:,} 行；查看建议后点击“确认导入”。"
        else:
            summary = f"{ready} / {len(self.selected)} 张表已通过；整批尚未导入。可复制提示词给 Agent，或取消勾选问题表。"
        if self.skipped:
            summary += " 读取失败：" + "；".join(self.skipped)
        self.ai_status.set(summary)
        self._update_controls()
        self._show_sheet_status()

    def _render_item(self, index):
        path, sheet, spec = self.items[index]
        review = self.reviews.get(index)
        state = self.item_status.get(index) or self.review_errors.get(index)
        if not state:
            if review:
                state = (f"可导入 · {review.report.stats.imported_rows:,} 行" if review.ready else "需处理：" + review.problem)
            else:
                state = "等待审查" if index in self.selected else "未勾选"
        values = ("☑" if index in self.selected else "☐", path.name, sheet,
                  self.LABELS[spec.kind] if spec else "待识别", state)
        if self.view.exists(str(index)):
            self.view.item(str(index), values=values)
        else:
            self.view.insert('', 'end', iid=str(index), values=values)

    def _show_sheet_status(self, event=None):
        selected = self.view.selection()
        lines = []
        if selected:
            index = int(selected[0])
            path, sheet, spec = self.items[index]
            lines = [f"{path.name} / {sheet}", self.view.set(str(index), "state")]
            review = self.reviews.get(index)
            if review:
                lines.append(f"{self.LABELS[review.spec.kind]}；表头第 {review.spec.header_row} 行")
                if review.report:
                    lines.append(review.report.summary_line())
                for field, col in review.spec.mapping.items():
                    lines.append(f"{FIELD_LABELS.get(field, field)} ← 第 {col + 1} 列：{review.headers[col] or '（空表头）'}")
                if review.report:
                    lines.extend(f"提醒：{issue.reason}" for issue in review.report.issues)
                if review.exclusions:
                    lines.append(f"本地规则自动排除 {len(review.exclusions)} 行，理由随导入记录保存。")
                lines.extend(f"说明：{notice}" for notice in review.notices)
        self.detail.configure(state="normal")
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', '\n'.join(lines))
        self.detail.configure(state="disabled")

    def _toggle_item(self, event):
        if self.ai_running or self.working or self._editor:
            return
        item = self.view.identify_row(event.y)
        if item and self.view.identify_column(event.x) == '#1':
            index = int(item)
            if index in self.completed:
                return
            if index in self.selected:
                self.selected.remove(index)
            else:
                self.selected.add(index)
            self._render_item(index)
            self._start_review()

    def _edit_current(self, event=None):
        if self.working or self._editor:
            return
        if event is not None:
            item = self.view.identify_row(event.y)
            if not item or self.view.identify_column(event.x) == '#1':
                return
        else:
            selection = self.view.selection()
            item = selection[0] if selection else None
        if item is None or int(item) in self.completed:
            return
        self._cancel_review()
        index = int(item)
        path, sheet, spec = self.items[index]

        def save(updated):
            self.items[index] = (path, sheet, updated)
            self.item_status.pop(index, None)
            self.review_errors.pop(index, None)
            self.reviews.pop(index, None)
            self.selected.add(index)
            self.manual.add(index)
            self._render_item(index)

        editor = ImportDialog(self.parent, path, spec=spec, sheet_name=sheet, on_save=save)
        self._editor = editor
        self._update_controls()
        editor.transient(self)

        def restore(event):
            if event.widget is editor and self.winfo_exists():
                self._editor = None
                self.grab_set()
                self.after_idle(self._start_review)
        editor.bind('<Destroy>', restore, add='+')

    def _copy_agent(self):
        indices = sorted(self.selected - self.completed)
        copy_agent_task(self, [(self.items[i][0], self.items[i][1]) for i in indices])

    def _import_selected(self) -> None:
        if self.ai_running or self.working or self._editor or not self.selected:
            return
        if not all(i in self.reviews and self.reviews[i].ready for i in self.selected):
            self._review_finished()
            return
        indices = sorted(self.selected)
        reviews = {i: self.reviews[i] for i in indices}
        self._set_working(True, "正在确认原文件未发生变化…")

        def verify(stop, report):
            fingerprints = {}
            for index in indices:
                check_cancelled(stop)
                path = reviews[index].spec.path
                if path not in fingerprints:
                    fingerprints[path] = file_hash(path)
            return {i for i in indices if fingerprints[reviews[i].spec.path] != reviews[i].fingerprint}

        def verified(changed):
            self._set_working(False)
            if changed:
                paths = {reviews[i].spec.path for i in changed}
                for i, (path, sheet, spec) in enumerate(self.items):
                    if path in paths:
                        self.reviews.pop(i, None)
                        self.review_errors[i] = "原文件已修改，旧审查结果失效；请重新审查。"
                        self.item_status.pop(i, None)
                self._review_finished()
                return
            self._import_prechecked([(reviews[i].spec, reviews[i].report) for i in indices],
                auto_reasons={(reviews[i].spec.sheet, str(reviews[i].spec.path)): reviews[i].exclusions for i in indices})

        self.tasks.submit('verify', verify, verified, self._work_failed)

    def _import_prechecked(self, work: list, paused_specs: list | None = None, auto_reasons: dict | None = None) -> None:
        work = [(spec, result) for spec, result in work if result is not None and not result.blocked]
        if not work:
            return
        paused_specs, auto_reasons = paused_specs or [], auto_reasons or {}
        old_sources = self.parent.store.sources()
        duplicates = {number for number, (spec, result) in enumerate(work) if any(
            source["kind"] == spec.kind and source["sheet"] == spec.sheet and
            (source["sha256"] == result.fingerprint or source["original_path"] == str(spec.path.resolve())) for source in old_sources)}
        replace = bool(duplicates) and messagebox.askyesno("已有导入记录",
            f"{len(duplicates)} 个工作表已有导入记录。是否替换这些记录？选择“否”将跳过它们，继续导入其他表。", parent=self)
        jobs, skipped = [], []
        for number, (spec, result) in enumerate(work):
            index = next(index for index, (path, sheet, _) in enumerate(self.items) if path == spec.path and sheet == spec.sheet)
            if number in duplicates and not replace:
                skipped.append(index)
            else:
                jobs.append((index, spec, result, number in duplicates, auto_reasons.get((spec.sheet, str(spec.path)), {})))
        path = self.parent.store.path
        def write(store, stop, report):
            outcomes = []
            for index, spec, result, replacement, reasons in jobs:
                if stop.is_set():
                    break
                report(f"写入 {spec.path.name}/{spec.sheet}")
                try:
                    count = store.import_sheet(spec, replace=replacement, exclusion_reasons=reasons,
                        cancelled=stop, expected_fingerprint=result.fingerprint,
                        progress=lambda row: report(f"写入 {spec.path.name}/{spec.sheet}：{row:,} 行"))
                    outcomes.append((index, count, ""))
                except Exception as exc:
                    if stop.is_set():
                        break
                    outcomes.append((index, 0, str(exc)))
            return outcomes, stop.is_set()
        def finish(result):
            outcomes, stopped = result
            self._set_working(False)
            failures = []
            for index in skipped:
                self.selected.discard(index)
                self.item_status[index] = "已跳过已有来源"
                self._render_item(index)
            for index, count, error in outcomes:
                if error:
                    self.item_status[index] = f"导入失败：{error}；可重试"
                    failures.append(error)
                else:
                    self.selected.discard(index)
                    self.completed.add(index)
                    excluded = next((len(reasons) for job_index, _, _, _, reasons in jobs if job_index == index), 0)
                    note = f"；自动排除 {excluded} 行" if excluded else ""
                    self.item_status[index] = f"已完成：导入 {count:,} 行{note}"
                self._render_item(index)
            self.parent.refresh_all()
            successful = sum(not error for _, _, error in outcomes)
            summary = f"成功导入 {successful} 张表。核对提醒会自动更新。"
            if paused_specs:
                summary += f" {len(paused_specs)} 张表有未解决疑点，未导入，可调整后重试。"
            if skipped:
                summary += f" 跳过 {len(skipped)} 个已有来源。"
            if failures:
                summary += " 失败原因：" + "；".join(failures[:3])
                if len(failures) > 3:
                    summary += f"；另有 {len(failures) - 3} 项，选中列表可查看具体原因。"
            if stopped:
                summary += " 已停止；当前未完成的工作表未写入。"
            self.ai_status.set(summary)
            self._update_controls()
            self._show_sheet_status()
            if not stopped:
                messagebox.showinfo("导入结果", summary, parent=self)
            if not paused_specs and not failures and not stopped:
                self.destroy()
        if requires_background([spec.path for _, spec, _, _, _ in jobs]):
            self._set_working(True, "正在本机写入…")
            stop = self.stop_requested
            def worker(cancelled, report):
                store = LedgerStore.connect_existing(path, readonly=False, cancelled=stop)
                try:
                    return write(store, stop, report)
                finally:
                    store.close()
            self.tasks.submit('write', worker, finish, self._work_failed, self.ai_status.set)
        else:
            finish(write(self.parent.store, Event(), lambda _: None))


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
        self.shortcuts = VoucherShortcuts(self)
        self.shortcuts.attach(self)
        self.tasks = BackgroundTasks(self)
        self.import_busy = False
        self.detail_offset = 0
        self.detail_total = 0
        self._checks_cache = None
        self.title(f"DudLedger · {DISPLAY_VERSION}")
        self.geometry("1160x690")
        self.minsize(820, 520)
        self.resizable(True, True)
        self.store: LedgerStore | None = None
        self.account: str | list[str] | None = None
        self.include_descendants = False
        self.account_label = tk.StringVar(value="全部科目")
        self.period_from = tk.StringVar(value="不限")
        self.period_to = tk.StringVar(value="不限")
        self.period_options: dict[str, tuple[str, str]] = {}
        self.balance_source = tk.StringVar(value="无余额表")
        self.balance_options: dict[str, int] = {}
        self.balance_calc_sources: dict[str, str] = {}
        self.report_source = {kind: tk.StringVar(value="无报表") for kind in REPORT_KINDS}
        self.report_options: dict[str, dict[str, int]] = {kind: {} for kind in REPORT_KINDS}
        self.search = tk.StringVar()
        self.detail_search = ""
        self.detail_conditions: dict[str, EntryCondition] = {}
        self.summary_search = tk.StringVar()
        self.detail_sort = "date"
        self.detail_descending = False
        self.detail_info = tk.StringVar()
        self.mark_search = tk.StringVar()
        self.mark_keys: dict[str, str] = {}
        self.mark_entry_ids: dict[str, int | None] = {}
        self.selected_mark_key: str | None = None
        self.status = tk.StringVar(value="从“文件”导入 Excel 即可开始；账套默认保存在原表格目录。")
        self.column_views: dict[str, list[ttk.Treeview]] = {kind: [] for kind in VIEW_COLUMNS}
        self.voucher_windows: set[tk.Toplevel] = set()
        self.review_windows: set[tk.Toplevel] = set()
        self._sidebar_expanded = False
        self._sidebar_mode = None
        self._show_sidebar_with_calculator = False
        self.note_hub = NoteHub(self)
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
        self.navigation_tooltip = NavigationTooltip(self)
        self.sidebar_icons = {}
        self.sidebar_buttons = []
        self.sidebar_groups = []
        self.sidebar_brand = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        ttk.Label(self.sidebar_brand, text="DudLedger Beta", style="Brand.TLabel").pack(anchor="w", padx=18, pady=(8, 1))
        ttk.Label(self.sidebar_brand, text="法律尽调 · 账簿核查", style="SidebarHint.TLabel").pack(anchor="w", padx=18, pady=(0, 5))
        self.sidebar_pages = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        self.sidebar_pages.pack(fill="x", pady=(5, 0))
        self.nav_buttons = []
        self.page_names = ("科目总览", "明细账", "财务报表", "关注凭证", "AI 尽调线索")
        self.page_ids = ("accounts", "entries", "reports", "marks", "review")
        nav_icons = ("accounts", "entries", "balance", "marks", "review")
        for index, (title, icon) in enumerate(zip(self.page_names, nav_icons)):
            if index == 3:
                self.sidebar_section_divider = ttk.Frame(self.sidebar_pages, height=22, style='Sidebar.TFrame')
                self.sidebar_section_divider.pack(fill='x', padx=9, pady=(8, 6))
                self.sidebar_section_divider.pack_propagate(False)
                ttk.Separator(self.sidebar_section_divider).pack(fill='x', pady=(10, 0))
            button = self._sidebar_button(self.sidebar_pages, title, icon, lambda page=self.page_ids[index]: self.select_page(page))
            if index in (0, 3):
                heading = ttk.Label(self.sidebar_pages, text="账簿" if index == 0 else "核查",
                                    style="SidebarHint.TLabel")
                self.sidebar_groups.append((heading, button))
            self.nav_buttons.append(button)
            if index == 3:
                self.workbench_button = self._sidebar_button(self.sidebar_pages, "问题工作台", "workbench", lambda: self.workbench.toggle())
        self.calculator_button = self._sidebar_button(self.sidebar_pages, "计算器", "calculator", lambda: self.workbench.show_calculator(toggle=True))
        self._sidebar_button(self.sidebar_pages, "尽调主体名单", "parties", self.open_related_parties)
        footer = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        footer.pack(side="bottom", fill="x", padx=9, pady=(5, 8))
        self.settings_button = self._sidebar_button(footer, "设置", "settings", self.open_settings, padx=0, side="left")
        self.book_name = tk.StringVar(value="未打开账套")
        self.book_button = self._sidebar_button(footer, "账套", "folder", self.open_book_menu, padx=(4, 0), side="left", expand=True)
        self.sidebar_footer_label = ttk.Label(footer, text="本地工作区", style="SidebarHint.TLabel")
        self.workspace = ttk.Frame(self.shell)
        self.workspace.grid(row=0, column=1, sticky="nsew")
        toolbar = ttk.Frame(self.workspace, padding=(16, 15))
        toolbar.pack(fill="x")
        for name in ("expand", "collapse"):
            self.sidebar_icons[name] = navigation_icon(self, name)
        self.sidebar_toggle = ttk.Button(toolbar, compound="image", width=0, command=self._toggle_sidebar)
        self.sidebar_toggle.pack(side="left", padx=(0, 6))
        self.navigation_tooltip.attach(self.sidebar_toggle, lambda: self.sidebar_toggle.cget("text"))
        self.page_title = tk.StringVar(value="开始核查")
        self.page_menu = ttk.Label(toolbar, textvariable=self.page_title, style="Intro.TLabel")
        self.page_menu.pack(side="left")
        self.import_cta = RoundedButton(toolbar, text="导入表格", command=self.import_file, variant="primary")
        self.import_cta.pack(side="right")
        self.book_name.trace_add("write", lambda *_: self.book_button.configure(text=f"账套：{self.book_name.get()}"))
        self.book_button.configure(text=f"账套：{self.book_name.get()}")
        ttk.Separator(self.workspace).pack(fill="x", padx=18)
        self.content = ttk.Frame(self.workspace)
        self.content.pack(fill="both", expand=True, padx=18, pady=(12, 0))
        self.tabs = ttk.Notebook(self.content, style="Workspace.TNotebook")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        self.workbench = RightWorkbench(self)
        self.voucher_actions = VoucherActions(self)
        self.voucher_actions.attach(self.workbench.voucher_view, lambda row: self.workbench.rows[row][1])
        self.content.bind("<Configure>", lambda _e: self.workbench.layout())
        self.utility_windows = {}
        frames = []
        self.page_frames = {}
        self.report_kind = tk.StringVar(value="balance_sheet")
        self.report_labels = REPORT_LABELS
        self.report_frames = {}
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
            elif title in REPORT_LABELS.values():
                if not self.report_frames:
                    self.report_page = ttk.Frame(self.tabs)
                    self.tabs.add(self.report_page, text="财务报表")
                    self.page_frames['reports'] = self.report_page
                    switch = ttk.Frame(self.report_page, padding=5)
                    switch.pack(fill='x')
                    for kind, label in REPORT_LABELS.items():
                        ttk.Radiobutton(switch, text=label, variable=self.report_kind, value=kind,
                                        command=self.switch_report, style='Segment.TRadiobutton').pack(side='left', padx=(0, 4))
                frame = ttk.Frame(self.report_page)
                kind = next(key for key, label in REPORT_LABELS.items() if label == title)
                self.report_frames[kind] = frame
                if kind == 'balance_sheet':
                    frame.pack(fill='both', expand=True)
            else:
                frame = ttk.Frame(self.tabs)
                self.tabs.add(frame, text=title)
                self.page_frames[self.page_ids[self.page_names.index(title)]] = frame
            frames.append(frame)
        summary_search_row = ttk.Frame(frames[0], padding=5)
        summary_search_row.pack(fill="x")
        ttk.Label(summary_search_row, text="查找科目").pack(side="left")
        summary_entry = ttk.Entry(summary_search_row, textvariable=self.summary_search)
        summary_entry.pack(side="left", fill="x", expand=True, padx=6)
        summary_entry.bind("<Return>", lambda _e: self.refresh_summary())
        ttk.Button(summary_search_row, text="查找", command=self.refresh_summary).pack(side="left")
        ttk.Button(summary_search_row, text="清除", command=self.clear_summary_filter).pack(side="left", padx=5)
        balance_top = ttk.Frame(frames[0], padding=5)
        balance_top.pack(fill="x")
        balance_hint = ttk.Frame(balance_top)
        balance_actions = ttk.Frame(balance_top)
        balance_hint.grid(row=0, column=0, sticky="w")
        balance_actions.grid(row=0, column=1, sticky="e")
        balance_top.columnconfigure(0, weight=1)
        balance_top.bind("<Configure>", lambda event: self._flow_bar(balance_top, balance_hint, balance_actions))
        self.sidebar_icons["expand-all"] = navigation_icon(self, "expand-all")
        self.sidebar_icons["collapse-all"] = navigation_icon(self, "collapse-all")
        ttk.Button(balance_actions, text="展开全部下级", image=self.sidebar_icons["expand-all"], compound="left",
                   style="Ghost.TButton", command=lambda: self.set_expansion(True)).pack(side="left", padx=(12, 2))
        ttk.Button(balance_actions, text="折叠全部下级", image=self.sidebar_icons["collapse-all"], compound="left",
                   style="Ghost.TButton", command=lambda: self.set_expansion(False)).pack(side="left")
        summary_more = ttk.Menubutton(balance_actions, text="更多 ▾")
        summary_more.pack(side="right")
        summary_menu = tk.Menu(summary_more, tearoff=False)
        summary_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(self, "balance"))
        summary_menu.add_command(label="记住当前展开", command=self.remember_expansion)
        summary_more.configure(menu=summary_menu)
        self.summary_view = tree(frames[0], VIEW_COLUMNS["balance"])
        self.summary_view.configure(show="tree headings")
        self.summary_view.heading("#0", text="下级科目")
        self.summary_view.column("#0", width=120, minwidth=100, stretch=False)
        self._register_column_view("balance", self.summary_view)
        self.summary_view.bind("<Button-1>", self.toggle_account)
        self.summary_view.bind("<<TreeviewOpen>>", self._after_expansion_change)
        self.summary_view.bind("<<TreeviewClose>>", self._after_expansion_change)
        self.summary_view.bind("<Double-1>", self.open_account)
        self.balance_tools = TableTools(self.summary_view, {"od", "oc", "pd", "pc", "yd", "yc", "cd", "cc"},
            source_for_row=lambda row: self.balance_calc_sources.get(row, row),
            panel_parent=self.workbench.calculator_host("科目总览"),
            on_toggle=lambda reveal: self.workbench.set_calculator(self.balance_tools, "科目总览", reveal))
        self.detail_controls = ttk.Frame(frames[1], padding=5)
        self.detail_controls.pack(fill="x")
        self.detail_controls.columnconfigure(0, weight=1)
        self.detail_scope_wrap = ttk.Frame(self.detail_controls)
        self.detail_scope_wrap.grid(row=0, column=0, sticky="w")
        self.detail_accounts_toggle = ttk.Button(self.detail_scope_wrap, text="显示科目树 ▸", width=0,
                                                style="Ghost.TButton", command=self.toggle_detail_accounts)
        self.detail_accounts_toggle.pack(side="left", padx=(0, 6))
        self.detail_scope = ttk.Label(self.detail_scope_wrap, textvariable=self.account_label, wraplength=300, justify="left")
        self.detail_scope.pack(side="left")
        self.detail_scope_clear = ttk.Button(self.detail_scope_wrap, text="×", width=2, style="Ghost.TButton",
                                             command=self.show_all_entries)
        self.detail_scope_clear.pack(side="left", padx=(2, 0))
        self.account_label.trace_add("write", lambda *_: self._refresh_scope_clear())
        self._refresh_scope_clear()
        self.detail_actions = ttk.Frame(self.detail_controls)
        self.detail_actions.grid(row=0, column=2, sticky="e")
        self.detail_more = ttk.Menubutton(self.detail_actions, text="更多 ▾", width=0)
        self.detail_more.pack(side="right")
        detail_menu = tk.Menu(self.detail_more, tearoff=False)
        detail_menu.add_command(label="显示全部科目", command=self.show_all_entries)
        detail_menu.add_command(label="筛选条件…", command=self._open_detail_filter)
        detail_menu.add_command(label="清除全部筛选", command=self.clear_filter)
        detail_menu.add_separator()
        detail_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(self, "detail"))
        detail_menu.add_command(label="导出当前明细 CSV…", command=self.export_csv)
        detail_menu.add_command(label="恢复日期顺序", command=self.reset_detail_sort)
        self.detail_more.configure(menu=detail_menu)
        self.detail_mark = ttk.Button(self.detail_actions, text="关注所选（0）", width=0,
                                      command=self.mark_selected_entry, state="disabled")
        self.detail_controls.bind("<Configure>", self._resize_detail_controls)
        self.detail_scope.bind("<Configure>", self._resize_detail_controls)
        self.detail_actions.bind("<Configure>", self._resize_detail_controls)
        self.detail_filter_bar = ttk.Frame(frames[1], padding=(6, 0, 6, 4))
        self.detail_filter_bar.bind("<Configure>", self._layout_filter_chips)
        paging = ttk.Frame(frames[1], padding=(6, 2))
        paging.pack(side="bottom", fill="x")
        self.detail_paging = ttk.Frame(paging)
        self.detail_prev = ttk.Button(self.detail_paging, text="‹ 上一页", width=0, style="Ghost.TButton",
                                      command=lambda: self.change_detail_page(-1), state="disabled")
        self.detail_prev.pack(side="left")
        self.detail_page_label = tk.StringVar(value="第 1 / 1 页")
        ttk.Label(self.detail_paging, textvariable=self.detail_page_label).pack(side="left", padx=5)
        self.detail_next = ttk.Button(self.detail_paging, text="下一页 ›", width=0, style="Ghost.TButton",
                                      command=lambda: self.change_detail_page(1), state="disabled")
        self.detail_next.pack(side="left")
        self.detail_status = ttk.Label(paging, textvariable=self.detail_info, style="Hint.TLabel", wraplength=440)
        self.detail_status.pack(side="left", fill="x", expand=True)
        self.detail_status.bind("<Configure>", self._resize_detail_status)
        self.detail_body = ttk.Panedwindow(frames[1], orient="horizontal")
        self.detail_body.pack(fill="both", expand=True)
        self.detail_accounts = AccountNavigation(self.detail_body, self._choose_detail_account)
        self.detail_table = ttk.Frame(self.detail_body)
        self.detail_body.add(self.detail_table, weight=1)
        self.detail_view = tree(self.detail_table, VIEW_COLUMNS["detail"])
        self.detail_filter_images = (filter_arrow(self.detail_view), filter_arrow(self.detail_view, active=True))
        self.detail_view.bind("<ButtonPress-1>", self._detail_filter_click)
        self.detail_tools = TableTools(self.detail_view, {"debit", "credit", "original"},
                                       panel_parent=self.workbench.calculator_host("明细账"),
                                       on_toggle=lambda reveal: self.workbench.set_calculator(self.detail_tools, "明细账", reveal))
        self.detail_selection = VoucherSelection(self.detail_view, lambda row: self.detail_voucher_keys.get(row), self._update_detail_mark)
        self.voucher_actions.attach(self.detail_view, lambda row: self.detail_voucher_keys.get(row))
        selection_bar(self.detail_table, self.detail_selection)
        self.detail_join = ttk.Button(self.detail_actions, text="加入问题…（0）", command=lambda: self.voucher_actions.choose_from_view(self.detail_view), state='disabled')
        self.detail_join.pack(side="right", padx=4)
        self.detail_mark.pack(side="right")

        self._register_column_view("detail", self.detail_view)
        self.detail_view.bind("<Double-1>", self.open_voucher)
        self.detail_view.bind("<<TreeviewSelect>>", self._update_detail_mark, add="+")
        for key in DETAIL_FILTER_LABELS:
            self.detail_view.heading(key, command=lambda column=key: self._open_detail_filter(column))
        self._update_detail_headings()
        self.report_views: dict[str, ttk.Treeview] = {}
        self.report_info: dict[str, tk.StringVar] = {}
        self.report_combos: dict[str, ttk.Combobox] = {}
        self.report_tools = {}
        for kind, frame in (("balance_sheet", frames[2]), ("income_statement", frames[3])):
            top = ttk.Frame(frame, padding=5)
            top.pack(fill="x")
            ttk.Label(top, text="报表来源", style="Hint.TLabel").pack(side="left")
            combo = ttk.Combobox(top, textvariable=self.report_source[kind], values=("无报表",), state="readonly", width=40)
            combo.pack(side="left", fill="x", expand=True, padx=8)
            self.report_combos[kind] = combo
            combo.bind("<<ComboboxSelected>>", lambda _e, report_kind=kind: self.refresh_report(report_kind))
            info = tk.StringVar(value="尚未导入报表。")
            self.report_info[kind] = info
            ttk.Label(frame, textvariable=info, padding=(8, 3), foreground=theme.MUTED, wraplength=760, justify="left").pack(fill="x")
            columns = [("left_item", "项目", 230), ("left_a", "金额 1", 125), ("left_b", "金额 2", 125), ("left_c", "金额 3", 125)]
            if kind == "balance_sheet":
                columns += [("right_item", "右侧项目", 230), ("right_a", "右侧金额 1", 125), ("right_b", "右侧金额 2", 125), ("right_c", "右侧金额 3", 125)]
            columns += [("source", "来源行", 260)]
            self.report_views[kind] = tree(frame, columns)
            self.report_views[kind].configure(displaycolumns=[key for key, _, _ in columns if key != "source"])
            self.report_tools[kind] = TableTools(self.report_views[kind], {"left_a", "left_b", "left_c", "right_a", "right_b", "right_c"},
                panel_parent=self.workbench.calculator_host(REPORT_LABELS[kind]),
                on_toggle=lambda reveal, report_kind=kind: self.workbench.set_calculator(self.report_tools[report_kind], REPORT_LABELS[report_kind], reveal))
        check_top = ttk.Frame(frames[4], padding=5)
        check_top.pack(fill="x")
        ttk.Label(check_top, text="这里只列需要核对的线索；金额差异不影响查看原始分录。", style="Hint.TLabel").pack(side="left")
        ttk.Button(check_top, text="设置核对范围", command=self.open_reconciliation).pack(side="right")
        self.check_view = tree(frames[4], [("level", "级别", 70), ("category", "类别", 130), ("subject", "对象", 250), ("detail", "说明", 650)])
        source_top = ttk.Frame(frames[5], padding=5)
        source_top.pack(fill="x")
        ttk.Button(source_top, text="添加表格", command=self.import_file, style="Accent.TButton").pack(side="left")
        ttk.Button(source_top, text="移除所选导入", command=self.delete_selected_source, style="Danger.TButton").pack(side="left", padx=(6, 0))
        ttk.Button(source_top, text="查看预检详情", command=self.show_precheck_detail).pack(side="left", padx=6)
        ttk.Button(source_top, text="导出导入质量报告…", command=self.export_import_report).pack(side="right")
        source_choice = ttk.Frame(frames[5], padding=(5, 2, 5, 6))
        source_choice.pack(fill="x")
        ttk.Label(source_choice, text="总览使用的科目余额表").pack(side="left")
        self.balance_combo = ttk.Combobox(source_choice, textvariable=self.balance_source, values=("无余额表",), state="readonly", width=45)
        self.balance_combo.pack(side="left", padx=8)
        self.balance_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_summary())
        ttk.Label(source_choice, text="切换来源只影响显示，不会合并不同期间。", style="Hint.TLabel").pack(side="left")
        self.source_view = tree(frames[5], [("file", "文件", 220), ("sheet", "工作表", 130), ("kind", "类型", 85), ("rows", "有效行", 80), ("precheck", "导入预检", 170), ("hash", "SHA-256", 220), ("path", "原始路径", 430)])
        self.source_view.bind("<Double-1>", lambda _event: self.show_precheck_detail())
        mark_top = ttk.Frame(frames[6], padding=5)
        mark_top.pack(fill="x")
        mark_search_row = ttk.Frame(mark_top)
        mark_search_row.pack(fill="x")
        ttk.Label(mark_search_row, text="查找").pack(side="left")
        mark_entry = ttk.Entry(mark_search_row, textvariable=self.mark_search)
        mark_entry.pack(side="left", fill="x", expand=True, padx=5)
        mark_entry.bind("<Return>", lambda _event: self.refresh_marks())
        ttk.Button(mark_search_row, text="筛选", style="Ghost.TButton", command=self.refresh_marks).pack(side="left")
        self.mark_note_toggle = ttk.Button(mark_search_row, text='编辑备注 ▸', style="Ghost.TButton", command=self.toggle_mark_note)
        self.mark_note_toggle.pack(side='left', padx=(8, 0))
        self.mark_join = ttk.Button(mark_search_row, text="加入问题…（0）", command=lambda: self.voucher_actions.choose_from_view(self.mark_view), state='disabled')
        self.mark_join.pack(side='right', padx=4)
        mark_more = ttk.Menubutton(mark_search_row, text='更多 ▾')
        mark_more.pack(side='right')
        mark_menu = tk.Menu(mark_more, tearoff=False)
        mark_menu.add_command(label='取消所选关注…', command=self.remove_selected_mark)
        mark_menu.add_command(label='确认所选变更凭证…', command=self.confirm_selected_mark)
        mark_menu.add_separator()
        mark_menu.add_command(label='更新关注凭证 MD', command=self.export_marks)
        mark_menu.add_command(label='另存为 MD…', command=lambda: self.export_marks(as_new=True))
        mark_more.configure(menu=mark_menu)
        self.mark_view = tree(frames[6], [("date", "日期", 100), ("voucher", "凭证号", 105), ("summary", "摘要", 250), ("amount", "金额", 150), ("accounts", "涉及科目", 210), ("note", "备注摘要", 220), ("status", "复核状态", 150)])
        self.mark_tools = TableTools(self.mark_view, set(), show_stats=False)
        self.mark_selection = VoucherSelection(self.mark_view, lambda row: self.mark_keys.get(row), self._update_mark_actions)
        self.voucher_actions.attach(self.mark_view, lambda row: self.mark_keys.get(row))
        self.mark_tools.menu.add_command(label='取消所选关注…', command=self.remove_selected_mark)
        CellTooltip(self.mark_view)
        selection_bar(frames[6], self.mark_selection)
        self.mark_view.bind("<<TreeviewSelect>>", self._load_selected_mark, add='+')
        self.mark_view.bind("<Double-1>", self.open_selected_mark_voucher)
        self.mark_note_frame = ttk.Frame(frames[6], padding=(6, 6))
        self.mark_note = tk.Text(self.mark_note_frame, height=4, wrap="word", undo=True, background=theme.SURFACE, relief="solid", borderwidth=1,
                                 highlightthickness=1, highlightcolor=theme.ACCENT, highlightbackground=theme.BORDER_STRONG)
        self.mark_note.pack(fill="x", pady=(4, 5))
        self.mark_note_status = tk.StringVar()
        ttk.Label(self.mark_note_frame, textvariable=self.mark_note_status, style="Hint.TLabel").pack(anchor="w")
        ttk.Button(self.mark_note_frame, text="重读已保存备注…", command=lambda: self.note_hub.reload(self.mark_note)).pack(anchor="e")
        from .jev_ui import ReviewPanel
        self.review_panel = ReviewPanel(frames[7], self)
        self.table_fit = WorkspaceTableFit(self, [self.summary_view, self.detail_view, *self.report_views.values(), self.mark_view])
        for view in (self.summary_view, self.detail_view, *self.report_views.values()):
            CellTooltip(view)
        self.tabs.bind("<<NotebookTabChanged>>", self._page_changed)
        status_bar = ttk.Frame(self.workspace)
        status_bar.pack(fill="x")
        self.busy_bar = ttk.Progressbar(status_bar, mode="indeterminate", style="Thin.Horizontal.TProgressbar", length=120)
        self.status_label = ttk.Label(status_bar, textvariable=self.status, anchor="w", style="Status.TLabel")
        self.status_label.pack(fill="x")
        self.welcome = ttk.Frame(self.content)
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        welcome_body = ttk.Frame(self.welcome)
        welcome_body.place(relx=0.5, rely=0.44, anchor="center")
        ttk.Label(welcome_body, text="从一份账簿开始", style="Welcome.TLabel").pack(pady=(0, 14))
        ttk.Label(welcome_body, text=f'DudLedger · {DISPLAY_VERSION}', foreground=theme.ACCENT).pack(pady=(0, 10))
        ttk.Label(welcome_body, text="报表、科目、明细与凭证，在一个工作区核查。", style="Hint.TLabel").pack(pady=(0, 28))
        actions = ttk.Frame(welcome_body)
        actions.pack()
        RoundedButton(actions, text="导入 Excel / CSV", variant="primary", command=self.import_file).pack(side="left", padx=5)
        RoundedButton(actions, text="打开已有账套", variant="secondary", command=self.open_book).pack(side="left", padx=5)
        ttk.Label(welcome_body, text="原表保持不变  ·  AI 辅助需确认后启用", style="Hint.TLabel").pack(pady=(22, 0))
        self.bind("<Configure>", self._resize_workspace, add="+")
        self.after_idle(self._resize_workspace)
        self._page_changed()

    def current_page(self):
        selected = self.tabs.select()
        return next((key for key, frame in self.page_frames.items() if str(frame) == selected), None)

    def select_page(self, page):
        self.tabs.select(self.page_frames[page])

    def switch_report(self):
        for kind, frame in self.report_frames.items():
            if kind == self.report_kind.get():
                frame.pack(fill='both', expand=True)
            else:
                frame.pack_forget()
        if hasattr(self, 'report_tools'):
            self.workbench.page_changed()

    def open_book_menu(self):
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label='打开账套…', command=self.open_book)
        menu.add_command(label='新建账套…', command=self.new_book)
        menu.add_separator()
        menu.add_command(label='导入记录…', command=lambda: self.open_utility('导入记录'))
        menu.add_command(label='核对提醒…', command=lambda: self.open_utility('核对提醒'))
        menu.add_separator()
        menu.add_command(label='退出当前账套', command=self.close_book)
        try:
            menu.tk_popup(self.sidebar.winfo_rootx() + self.sidebar.winfo_width(), self.sidebar.winfo_rooty() + self.sidebar.winfo_height() - 150)
        finally:
            menu.grab_release()

    def _page_changed(self, _event=None) -> None:
        selected = self.tabs.select()
        if not selected:
            return
        index = self.tabs.index(selected)
        self.page_title.set(self.page_names[index] if self.store else "开始核查")
        for page, button in enumerate(self.nav_buttons):
            button.state(["selected" if page == index else "!selected"])
        self._resize_workspace()
        self.workbench.page_changed()
        if self.current_page() == "review":
            self.review_panel.refresh()

    def _detail_calculator_active(self) -> bool:
        return (self.workbench.visible and self.workbench.mode == "calculator"
                and self.workbench.active_tools is self.detail_tools and self.current_page() == "entries")

    def _detail_calculator_changed(self, visible: bool) -> None:
        if not visible:
            self._show_sidebar_with_calculator = False
        self._resize_workspace()

    def _toggle_sidebar(self) -> None:
        self._sidebar_expanded = not self._sidebar_expanded
        self._resize_workspace()

    def busy_start(self, label: str | None = None) -> None:
        """Show indeterminate progress in the status bar (and mark the main CTA)."""
        self._busy_count = getattr(self, "_busy_count", 0) + 1
        if self._busy_count == 1:
            if not self.busy_bar.winfo_manager():
                self.busy_bar.pack(side="left", padx=(18, 6), before=self.status_label)
            self.busy_bar.start(80)
            if label:
                self._busy_prev_status = self.status.get()
                self.status.set(label)
            self.import_cta.configure(state="disabled")

    def busy_stop(self) -> None:
        self._busy_count = max(0, getattr(self, "_busy_count", 0) - 1)
        if self._busy_count == 0:
            self.busy_bar.stop()
            self.busy_bar.pack_forget()
            if hasattr(self, "_busy_prev_status"):
                self.status.set(self._busy_prev_status)
                del self._busy_prev_status
            self.import_cta.configure(state="normal")

    def refresh_workbench_badge(self, count: int) -> None:
        text = f"问题工作台（{count}）" if count else "问题工作台"
        self.workbench_button.configure(text=text)

    def _sidebar_button(self, parent, title, icon, command, *, padx=9, side=None, expand=False):
        image = self.sidebar_icons[icon] = navigation_icon(self, icon)
        button = ttk.Button(parent, text=title, image=image, width=0, command=command)
        if side:
            button.pack(side=side, fill="x" if expand else "none", expand=expand, padx=padx, pady=1)
        else:
            button.pack(fill="x", padx=padx, pady=1)
        self.sidebar_buttons.append(button)
        self.navigation_tooltip.attach(button, lambda: str(button.cget("text")) if self._sidebar_mode == "icons" else "")
        return button

    def _render_sidebar(self, mode: str) -> None:
        if mode == self._sidebar_mode:
            return
        self.navigation_tooltip.hide()
        self._sidebar_mode = mode
        if mode == "hidden":
            self.sidebar.grid_remove()
            return
        expanded = mode == "text"
        self.sidebar.grid()
        if expanded:
            self.sidebar_brand.pack(fill="x", before=self.sidebar_pages)
            for heading, button in self.sidebar_groups:
                heading.pack(anchor="w", padx=20, pady=(4, 2), before=button)
            self.sidebar_footer_label.pack(anchor="w", padx=11, pady=(4, 0))
        else:
            self.sidebar_brand.pack_forget()
            for heading, _button in self.sidebar_groups:
                heading.pack_forget()
            self.sidebar_footer_label.pack_forget()
        for button in self.sidebar_buttons:
            button.configure(style="Nav.TButton" if expanded else "Rail.Nav.TButton",
                             compound="left" if expanded else "image")

    def _resize_workspace(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        calculator = self._detail_calculator_active()
        hidden = False
        mode = "hidden" if hidden else ("text" if self._sidebar_expanded else "icons")
        sidebar_width = max(184, self.controller.font_size * 13)
        self.sidebar.configure(width=sidebar_width if mode == "text" else 56)
        self._render_sidebar(mode)
        if mode == 'text':
            if self.winfo_height() < 650:
                self.sidebar_brand.pack_forget()
                self.sidebar_footer_label.pack_forget()
            elif not self.sidebar_brand.winfo_manager():
                self.sidebar_brand.pack(fill='x', before=self.sidebar_pages)
                self.sidebar_footer_label.pack(anchor='w', padx=11, pady=(4, 0))
        collapse = self._sidebar_expanded
        self.sidebar_toggle.configure(
            text="折叠为图标" if collapse else "展开侧栏文字",
            image=self.sidebar_icons["collapse" if collapse else "expand"],
        )
        self.workbench.layout()
        self.table_fit.schedule()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        # 文件菜单只保留最高频的三个动作；其余功能在各页面内（导入记录、明细账“更多”、关注凭证页）。
        files = tk.Menu(menu, tearoff=False)
        files.add_command(label="导入 Excel…", command=self.import_file, accelerator="⌘I")
        files.add_command(label="打开账套…", command=self.open_book, accelerator="⌘O")
        files.add_separator()
        files.add_command(label="退出当前账套", command=self.close_book)
        menu.add_cascade(label="文件", menu=files)
        review = tk.Menu(menu, tearoff=False)
        macos = self.tk.call("tk", "windowingsystem") == "aqua"
        review.add_command(label="切换侧栏", command=self._toggle_sidebar,
                           accelerator="⌘⇧L" if macos else "Ctrl+Shift+L")
        review.add_command(label="科目总览", command=lambda: self.select_page("accounts"))
        review.add_command(label="明细账", command=lambda: self.select_page("entries"))
        review.add_command(label="财务报表", command=lambda: self.select_page("reports"))
        review.add_command(label="关注凭证", command=lambda: self.select_page("marks"))
        review.add_command(label="问题工作台", command=self.workbench.show)
        review.add_command(label="AI 尽调线索", command=lambda: (self.review_panel.refresh(), self.select_page("review")))
        review.add_command(label="筛选全部凭证…", command=lambda: (self.select_page("review"), self.review_panel.new_batch()))
        review.add_command(label="尽调主体名单…", command=self.open_related_parties)
        review.add_command(label="核对提醒…", command=lambda: self.open_utility("核对提醒"))
        review.add_command(label="核对范围…", command=self.open_reconciliation)
        menu.add_cascade(label="查看", menu=review)
        options = tk.Menu(menu, tearoff=False)
        options.add_command(label="偏好设置…", command=self.open_settings)
        options.add_command(label="新账套默认文件夹…", command=self.choose_book_folder)
        options.add_command(label="AI审查设置", command=self.open_review_rules)
        menu.add_cascade(label="设置", menu=options)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="重新查看使用引导", command=self.show_usage_tour)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menu)
        for dialog in self.utility_windows.values():
            dialog.configure(menu=menu)
        self.bind("<Command-i>", lambda _event: self.import_file())
        self.bind("<Command-o>", lambda _event: self.open_book())
        modifier = "Command" if macos else "Control"
        self.bind(f"<{modifier}-Shift-L>", lambda _event: self._toggle_sidebar())

    def open_utility(self, title: str) -> None:
        dialog = self.utility_windows[title]
        dialog.deiconify()
        dialog.lift()
        if title == "核对提醒":
            self.refresh_checks()

    def _register_column_view(self, kind: str, view: ttk.Treeview) -> None:
        self.column_views[kind].append(view)
        saved = self.store.display_columns(kind) if self.store else None
        view.configure(displaycolumns=visible_columns(kind, saved))
        view.dashed_grid.schedule()

    def _change_columns(self, kind: str, selected: list[str]) -> None:
        selected = visible_columns(kind, selected)
        if self.store:
            self.store.save_display_columns(kind, selected)
        self.column_views[kind] = [view for view in self.column_views[kind] if view.winfo_exists()]
        for view in self.column_views[kind]:
            view.configure(displaycolumns=selected)
            view.dashed_grid.schedule()
            if hasattr(view, "cell_tools"):
                view.cell_tools.schedule()
        self.table_fit.schedule()

    def _open_columns(self, parent: tk.Widget, kind: str) -> None:
        if not self.store:
            return
        ColumnDialog(parent, kind, visible_columns(kind, self.store.display_columns(kind)), lambda selected: self._change_columns(kind, selected))

    def open_settings(self) -> None:
        SettingsDialog(self)

    def show_usage_tour(self):
        return self.controller.show_usage_tour(self)

    def open_review_rules(self) -> None:
        if self.import_busy or self.review_panel.preparing:
            self.status.set("请完成或取消当前本地处理后再修改账套设置。")
            return
        if not self.store:
            messagebox.showinfo("请打开账套", "规则保存在当前账套，请先打开账套。", parent=self)
            return
        from .jev_ui import ReviewRulesDialog
        ReviewRulesDialog(self)

    def open_reconciliation(self) -> None:
        if self.import_busy or self.review_panel.preparing:
            self.status.set("请完成或取消当前本地处理后再修改账套设置。")
            return
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        ReconciliationDialog(self)

    def open_related_parties(self) -> None:
        if self.import_busy or self.review_panel.preparing:
            self.status.set("请完成或取消当前本地处理后再修改账套设置。")
            return
        if not self.store:
            messagebox.showinfo("先选择项目", "请先新建或打开本地项目。", parent=self)
            return
        RelatedPartiesDialog(self)

    def _close(self) -> None:
        if not self.workbench.flush():
            return
        for dialog in list(self.voucher_windows | self.review_windows):
            if dialog.winfo_exists() and not dialog.request_close():
                return
        self.review_panel.cancel()
        self.tasks.cancel_all()
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

    def close_book(self) -> None:
        """退出当前账套：关闭数据库回到欢迎页，不退出程序。"""
        if not self.store:
            self.status.set("当前没有打开的账套。")
            return
        if not self.workbench.flush():
            return
        for dialog in list(self.voucher_windows | self.review_windows):
            if dialog.winfo_exists() and not dialog.request_close():
                return
        self.review_panel.cancel()
        self.voucher_windows.clear()
        for child in list(self.winfo_children()):
            if isinstance(child, (ImportDialog, BatchImportDialog)):
                child.destroy()
        self.tasks.cancel_all()
        self.import_busy = False
        self.import_owner = None
        self._checks_cache = None
        self._check_render_token = None
        self._summary_render_token = None
        self.controller.book_windows.pop(self.store.path.resolve(), None)
        self.store.close()
        self.store = None
        self.detail_accounts.reset()
        self.note_hub.unbind(self.mark_note)
        self.workbench.reset()
        self.workbench.hide()
        self.detail_tools.clear()
        self.balance_tools.clear()
        for tools in self.report_tools.values():
            tools.clear()
        self.detail_selection.set_scope(None)
        self.mark_selection.set_scope(None)
        self.account = None
        self.include_descendants = False
        self.account_label.set("全部科目")
        self._reset_detail_filters()
        self.summary_search.set("")
        self.balance_source.set("无余额表")
        for choice in self.report_source.values():
            choice.set("无报表")
        self.book_name.set("未打开账套")
        self.refresh_workbench_badge(0)
        self.title(f"DudLedger · {DISPLAY_VERSION}")
        self.refresh_all()
        self.review_panel.batch_id = None
        self.review_panel.job = None
        self.review_panel.refresh_rows()
        self.welcome.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.select_page("accounts")
        self._page_changed()
        self.status.set("已退出当前账套；可导入 Excel 或打开已有账套。")

    def _use_book(self, path: Path) -> None:
        if self.store and not self.workbench.flush():
            return
        try:
            new_store = LedgerStore(path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)
            return
        self.store = new_store
        self.detail_accounts.reset()
        self.toggle_detail_accounts(self.store.setting("detail_account_tree", "hide") == "show", persist=False)
        self.detail_selection.set_scope(None)
        self.mark_selection.set_scope(None)
        self.workbench.reset()
        self.detail_offset = self.detail_total = 0
        self._checks_cache = None
        self._check_render_token = None
        self._summary_render_token = None
        self.review_panel._totals_cache = None
        self.review_panel._stale_token = None
        name = path.stem
        self.book_name.set(name if len(name) <= 24 else name[:23] + "…")
        self.welcome.place_forget()
        for kind, views in self.column_views.items():
            self.column_views[kind] = [view for view in views if view.winfo_exists()]
            for view in self.column_views[kind]:
                view.configure(displaycolumns=visible_columns(kind, self.store.display_columns(kind)))
        self.account = None
        self.account_label.set("全部科目")
        self._reset_detail_filters()
        self.summary_search.set("")
        self.include_descendants = False
        self.balance_source.set("无余额表")
        for choice in self.report_source.values():
            choice.set("无报表")
        self.title(f"DudLedger · Beta · {path.name}")
        self.refresh_all()
        self.review_panel.refresh()
        self.select_page("entries" if self.store.setting("home_view", "summary") == "detail" else "accounts")
        self._page_changed()

    def import_file(self) -> None:
        if self.import_busy or self.review_panel.preparing:
            self.status.set("请完成或取消当前本地处理后再导入其他表格。")
            return
        chosen = filedialog.askopenfilenames(parent=self, title="选择表格（可多选文件）", filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.xls *.csv"), ("所有文件", "*")])
        if chosen:
            if not self.store:
                folder = self.controller.import_book_folder(Path(chosen[0]))
                name = Path(chosen[0]).stem[:80] or "新账套"
                while True:
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        index = 1
                        while True:
                            path = folder / f"{name}{'' if index == 1 else '-' + str(index)}.sqlite"
                            try:
                                path.touch(exist_ok=False)
                                break
                            except FileExistsError:
                                index += 1
                        self.controller.open_book(path, self)
                        break
                    except OSError as exc:
                        messagebox.showerror("无法在此目录创建账套", f"{exc}\n请选择其他保存文件夹。", parent=self)
                        alternative = filedialog.askdirectory(parent=self, title="选择账套保存文件夹", initialdir=str(Path(chosen[0]).parent))
                        if not alternative:
                            return
                        folder = Path(alternative).expanduser()
                if not self.store:
                    return
            BatchImportDialog(self, [Path(path) for path in chosen])

    def choose_book_folder(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="选择新账套默认保存文件夹", initialdir=str(self.controller.book_folder))
        if chosen:
            self.controller.preferences['book_folder'] = str(Path(chosen).resolve())
            self.controller.preferences['book_location_mode'] = 'fixed'
            self.controller._save_preferences()
            self.status.set(f"新账套将保存在：{self.controller.book_folder}")

    def delete_selected_source(self) -> None:
        if self.import_busy or self.review_panel.preparing:
            self.status.set("正在处理账套，请完成或取消当前任务后再移除来源。")
            return
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
        if self.store.large_book():
            self.import_busy = True
            self.import_owner = self
            path = self.store.path
            self.status.set("正在移除所选来源；可继续浏览，退出账套可停止。")
            def work(cancelled, report):
                store = LedgerStore.connect_existing(path, readonly=False, cancelled=cancelled)
                try:
                    return store.delete_source(source_id)
                finally:
                    store.close()
            def finish(_):
                self.import_busy = False
                self.import_owner = None
                self.refresh_all()
                self.status.set(f'已移除 {source["filename"]} / {source["sheet"]}；原始文件未改动。')
            def failed(exc):
                self.import_busy = False
                self.import_owner = None
                messagebox.showerror("移除失败", f"本次移除未完成，可重试。\n{exc}", parent=self)
            self.tasks.submit('remove_source', work, finish, failed)
            return
        self.store.delete_source(source_id)
        self.refresh_all()
        self.status.set(f'已从账套移除 {source["filename"]} / {source["sheet"]}；原始文件未改动。')

    def show_precheck_detail(self) -> None:
        if not self.store or not self.source_view.selection():
            messagebox.showinfo("先选择表格", "请在导入记录中选中一条导入记录。", parent=self)
            return
        source_id = int(self.source_view.selection()[0])
        source = next((row for row in self.store.sources() if row["id"] == source_id), None)
        if source is None:
            return
        PrecheckDetailWindow(self, self.store.precheck_for_source(source_id), source)

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
        if not chosen:
            return
        filters = self._detail_query_filters()
        output, path = Path(chosen), self.store.path
        def export(cancelled, report):
            store = LedgerStore.connect_existing(path, cancelled=cancelled)
            temporary = None
            try:
                store.db.execute("BEGIN")
                with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".csv", delete=False) as handle:
                    temporary = Path(handle.name)
                count = store.export_entries(temporary, **filters)
                check_cancelled(cancelled)
                os.replace(temporary, output)
                return count
            finally:
                store.close()
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        def done(count):
            self.status.set(f"已导出全部 {count:,} 条匹配明细。")
            messagebox.showinfo("导出完成", f"已导出 {count:,} 行。", parent=self)
        if self.store.large_book():
            self.status.set("正在导出全部匹配明细…")
            self.tasks.submit('export', export, done,
                lambda exc: messagebox.showerror("导出失败", f"原文件已保留。\n{exc}", parent=self))
        else:
            try:
                done(export(Event(), lambda _: None))
            except Exception as exc:
                messagebox.showerror("导出失败", f"原文件已保留。\n{exc}", parent=self)

    def mark_selected_entry(self) -> None:
        self.mark_voucher_keys(self.detail_selection.action_keys())

    def mark_voucher_keys(self, keys) -> None:
        if not self.store or not keys or not self.note_hub.flush():
            return
        try:
            result = issue_store.mark_vouchers(self.store, keys)
            for key in keys:
                self.update_mark_indicators(key)
                self.note_hub.broadcast(key)
            self.status.set(f'新增关注 {result["added"]} 张，已关注 {result["duplicates"]} 张')
        except Exception as exc:
            messagebox.showerror('关注失败', str(exc), parent=self)

    def refresh_marks(self, select_key: str | None = None) -> None:
        if not self.note_hub.flush():
            return
        self.tasks.cancel("marks")
        if self.store and self.store.large_book():
            self._read_task("marks", read_marks, lambda result: self._render_marks(result[0], select_key, result[1]))
        else:
            marks, summaries = read_marks(self.store) if self.store else ([], {})
            self._render_marks(marks, select_key, summaries)

    def _render_marks(self, marks, select_key, summaries=None):
        if not self.note_hub.flush():
            return
        self.mark_tools.reset_selection()
        self.mark_view.delete(*self.mark_view.get_children())
        self.mark_keys = {}
        self.mark_entry_ids = {}
        self.note_hub.unbind(self.mark_note)
        self.selected_mark_key = None
        self.mark_note.delete('1.0', 'end')
        if not self.store:
            return
        needle = self.mark_search.get().strip().lower()
        self.mark_selection.set_scope((str(self.store.path), needle))
        self.mark_selection.checked.intersection_update(mark['key'] for mark in marks)
        if summaries is None:
            summaries = summaries_for_keys(self.store, [mark['key'] for mark in marks])
        for index, mark in enumerate(marks):
            row = summaries[mark['key']]
            status = '；'.join(part for part in (mark['status'] if mark['status'] != '正常' else '', row['status'] if not row['entry_id'] else '', row['amount_status']) if part)
            searchable = ' '.join(str(value) for value in row.values()) + ' ' + mark['note'] + ' ' + status
            if needle and needle not in searchable.lower():
                continue
            iid = str(index)
            self.mark_keys[iid] = mark['key']
            self.mark_entry_ids[iid] = row['entry_id']
            self.mark_view.insert('', 'end', iid=iid, values=(row['date'], row['voucher'], row['summary'], row['amount'], row['accounts'], mark['note'].replace('\n', ' '), status or '正常'))
            if mark['key'] == select_key:
                self.mark_view.selection_set(iid)
                self.mark_view.see(iid)
        self.mark_selection.refresh()
        self._load_selected_mark()
        self._sync_empty_states()

    def open_selected_mark_voucher(self, _event: object = None) -> None:
        if _event is not None and self.mark_view.identify_column(_event.x) == "#0":
            return "break"
        row = self.mark_view.identify_row(_event.y) if _event is not None else next(iter(self.mark_view.selection()), None)
        if row and row in self.mark_keys:
            self.workbench.open_key(self.mark_keys[row])

    def _selected_mark_key(self) -> str | None:
        keys = self.mark_selection.action_keys()
        return keys[0] if len(keys) == 1 else None

    def _load_selected_mark(self, _event: object = None) -> None:
        key = self._selected_mark_key()
        if key == self.selected_mark_key:
            return
        if not self.note_hub.flush():
            previous = next((row for row, value in self.mark_keys.items() if value == self.selected_mark_key), None)
            if previous:
                self.mark_view.selection_set(previous)
            return
        self.note_hub.unbind(self.mark_note)
        self.selected_mark_key = key
        self.mark_note.delete("1.0", "end")
        if key and self.store:
            self.note_hub.bind(self.mark_note, self.mark_note, key, self.mark_note_status,
                               on_saved=lambda: self.update_mark_indicators(key))

    def edit_selected_mark(self) -> None:
        self.note_hub.flush()

    def update_mark_indicators(self, key):
        if not self.store:
            return
        mark = issue_store.note_record(self.store, key)
        for row in self.detail_view.get_children():
            # Update existing rows in place: do not reset cell selection or scroll.
            if getattr(self, "detail_voucher_keys", {}).get(row) == key:
                self.detail_view.set(row, "marked", "●" if mark else "")
                self.detail_view.item(row, tags=("marked",) if mark else ())
        if mark is not None and key not in self.mark_keys.values() and not getattr(self, "_marks_refresh_scheduled", False):
            self._marks_refresh_scheduled = True
            def refresh_later():
                self._marks_refresh_scheduled = False
                if self.store:
                    self.refresh_marks(select_key=self.selected_mark_key)
            self.after_idle(refresh_later)
        for row, row_key in self.mark_keys.items():
            if row_key == key and self.mark_view.exists(row) and mark:
                self.mark_view.set(row, "note", mark["note"].replace("\n", " "))

    def locate_issue_voucher(self, key):
        if not self.store or not self.workbench.flush():
            return
        item = issue_store.voucher_preview(self.store, key)
        entry_id = item["entry_id"]
        if entry_id is None:
            self.status.set("原凭证未匹配，可在工作台双击打开归集时快照。")
            return
        if self.detail_view.exists(str(entry_id)):
            self.select_page("entries")
            self.detail_view.selection_set(str(entry_id))
            self.detail_view.see(str(entry_id))
            return
        if (self.account or self.detail_search or self.detail_conditions or any(self._period_bounds())):
            if not messagebox.askyesno("定位凭证", "目标分录不在当前显示范围内。清除科目和筛选，并按日期定位该分录？", parent=self):
                return
        self.account = None
        self.include_descendants = False
        self.account_label.set("全部科目")
        self._reset_detail_filters()
        self.detail_sort, self.detail_descending = "date", False
        self.detail_locate_target = entry_id
        self.select_page("entries")
        self.refresh_details()

    def confirm_selected_mark(self) -> None:
        if not self.store or not self.workbench.flush():
            return
        keys = self.mark_selection.action_keys()
        marks = {item['key']: item for item in list_marks(self.store)}
        valid = [key for key in keys if key in marks and marks[key]['status'] != '未匹配']
        changed = [key for key in valid if marks[key]['status'] == '内容变化']
        if changed and not messagebox.askyesno('确认凭证内容', f'已核对 {len(changed)} 张内容变化的凭证，并确认继续关注？', parent=self):
            return
        from .voucher_notes import voucher_signature
        try:
            with self.store.db:
                for key in valid:
                    self.store.db.execute('UPDATE voucher_marks SET signature=? WHERE voucher_key=?', (voucher_signature(marks[key]['lines']), key))
            self.refresh_marks(select_key=self.selected_mark_key)
            self.status.set(f'已确认 {len(valid)} 张，未匹配跳过 {len(keys) - len(valid)} 张')
        except Exception as exc:
            messagebox.showerror('确认失败', str(exc), parent=self)

    def remove_selected_mark(self) -> None:
        if not self.store or not self.workbench.flush():
            return
        keys = self.mark_selection.action_keys()
        if not keys:
            return
        if not messagebox.askyesno('取消关注', f'取消 {len(keys)} 张凭证的关注并删除其备注？问题关联及归集快照保留。', parent=self):
            return
        try:
            with self.store.db:
                self.store.db.executemany('DELETE FROM voucher_marks WHERE voucher_key=?', [(key,) for key in keys])
            self.note_hub.unbind(self.mark_note)
            for key in keys:
                self.note_hub.broadcast(key)
                self.update_mark_indicators(key)
            self.mark_selection.clear()
            self.refresh_marks()
            self.status.set(f'已取消 {len(keys)} 张凭证的关注，问题关联保留')
        except Exception as exc:
            messagebox.showerror('取消关注失败', str(exc), parent=self)

    def toggle_mark_note(self):
        if self.mark_note_frame.winfo_manager():
            if self.note_hub.flush():
                self.mark_note_frame.pack_forget()
                self.mark_note_toggle.configure(text='编辑备注 ▸')
        elif len(self.mark_selection.action_keys()) == 1:
            self.mark_note_frame.pack(side='bottom', fill='x', before=self.mark_view.master)
            self.mark_note_toggle.configure(text='编辑备注 ▾')
            self._load_selected_mark()

    def _update_mark_actions(self):
        count = len(self.mark_selection.action_keys())
        self.mark_join.configure(text=f'加入问题…（{count}）', state='normal' if count else 'disabled')
        self.mark_note_toggle.configure(state='normal' if count == 1 else 'disabled')
        if hasattr(self, 'mark_note'):
            if count != 1 and self.mark_note_frame.winfo_manager():
                if self.note_hub.flush():
                    self.mark_note_frame.pack_forget()
                    self.mark_note_toggle.configure(text='编辑备注 ▸')
            self._load_selected_mark()

    def export_marks(self, as_new: bool = False) -> None:
        if not self.workbench.flush():
            return
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
        self.refresh_summary()

    def clear_filter(self) -> None:
        self._reset_detail_filters()
        self.refresh_details()

    def submit_detail_search(self) -> None:
        keyword = self.search.get().strip()
        self.detail_search = ''
        self._set_detail_filter('summary', EntryCondition('summary', 'contains', (keyword,)) if keyword else None)

    def _has_detail_filter(self, column: str) -> bool:
        if column == "period":
            return any(self._period_bounds())
        return column in self.detail_conditions

    def _open_detail_filter(self, column: str | None = None) -> None:
        if self.store:
            initial = column or next((key for key in DETAIL_FILTER_LABELS if self._has_detail_filter(key)), "date")
            DetailFilterDialog(self, DETAIL_FILTER_LABELS, initial)

    def _detail_filter_click(self, event):
        column = heading_filter_column(self.detail_view, event.x, event.y)
        if column is not None:
            self._open_detail_filter(column)
            return "break"

    def _set_detail_filter(self, column: str, condition: EntryCondition | tuple[str, str] | None) -> None:
        if column == "period":
            start, end = condition or ("不限", "不限")
            self.period_from.set(start)
            self.period_to.set(end)
        elif condition is None:
            self.detail_conditions.pop(column, None)
        else:
            self.detail_conditions[column] = condition
        self.detail_offset = 0
        self.refresh_details()

    def _refresh_scope_clear(self) -> None:
        self.detail_scope_clear.configure(state="normal" if getattr(self, "account", None) else "disabled")

    def _update_detail_mark(self, _event=None) -> None:
        selection = getattr(self, 'detail_selection', None)
        count = len(selection.action_keys()) if selection else 0
        self.detail_mark.configure(text=f'关注所选（{count}）', state='normal' if count else 'disabled',
                                   style='Accent.TButton' if count else 'TButton')
        if hasattr(self, 'detail_join'):
            self.detail_join.configure(text=f'加入问题…（{count}）', state='normal' if count else 'disabled')

    def reset_detail_sort(self) -> None:
        self.detail_sort = "date"
        self.detail_descending = False
        self.refresh_details()

    def sort_details(self, column: str, descending: bool | None = None) -> None:
        if column not in ENTRY_SORT_COLUMNS:
            return
        self.detail_descending = descending if descending is not None else (not self.detail_descending if column == self.detail_sort else False)
        self.detail_sort = column
        self.refresh_details()

    def show_all_entries(self) -> None:
        self.account = None
        self.include_descendants = False
        self.account_label.set("全部科目")
        self.refresh_details()

    @staticmethod
    def _period_sort_key(pair: tuple[str, str]) -> tuple[int, int]:
        def as_int(value: str) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
        return as_int(pair[0]), as_int(pair[1])

    def _period_bounds(self) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
        """起止均为闭区间（含当期）；只填一侧表示该侧不限；起止填反自动对调。"""
        start = self.period_options.get(self.period_from.get())
        end = self.period_options.get(self.period_to.get())
        if start and end and self._period_sort_key(start) > self._period_sort_key(end):
            start, end = end, start
        return start, end

    def _period_label(self) -> str:
        reverse = {value: key for key, value in self.period_options.items()}
        start, end = self._period_bounds()
        if start and end:
            return f"{reverse.get(start, self.period_from.get())} 至 {reverse.get(end, self.period_to.get())}"
        if start:
            return f"{reverse.get(start, self.period_from.get())} 起"
        if end:
            return f"至 {reverse.get(end, self.period_to.get())}"
        return "全部期间"

    def _reset_detail_filters(self) -> None:
        self.search.set("")
        self.detail_search = ""
        self.detail_conditions.clear()
        self.period_from.set("不限")
        self.period_to.set("不限")

    def _sync_empty_states(self) -> None:
        if not hasattr(self, "_empty_states"):
            self._empty_states = {
                "accounts": EmptyState(self.summary_view.master, "accounts",
                                       "还没有科目总览", "导入科目余额表或序时账后自动出现。",
                                       "导入表格", self.import_file),
                "entries_plain": EmptyState(self.detail_view.master, "entries",
                                            "暂无分录", "导入序时账后在此查看全部明细。",
                                            "导入表格", self.import_file),
                "entries_filtered": EmptyState(self.detail_view.master, "filter",
                                               "没有符合筛选条件的分录", "调整或清除筛选条件后再看。",
                                               "清除筛选", self.clear_filter),
                "marks": EmptyState(self.mark_view.master, "marks",
                                    "暂无关注凭证", "在明细账勾选凭证后点『关注所选』。",
                                    "去明细账", lambda: self.select_page("entries")),
            }
            for kind in REPORT_KINDS:
                self._empty_states[f"report_{kind}"] = EmptyState(
                    self.report_views[kind].master, "balance",
                    "尚未导入报表", "导入后在此查看；可在上方切换报表类型。",
                    "导入表格", self.import_file)
        states = self._empty_states
        if self.summary_view.get_children():
            states["accounts"].hide()
        else:
            states["accounts"].show()
        if self.detail_view.get_children():
            states["entries_plain"].hide()
            states["entries_filtered"].hide()
        else:
            filter_count = len(self.detail_conditions) + bool(self.detail_search) + self._has_detail_filter("period")
            (states["entries_filtered"] if filter_count and self.store else states["entries_plain"]).show()
            (states["entries_plain"] if filter_count and self.store else states["entries_filtered"]).hide()
        for kind in REPORT_KINDS:
            state = states[f"report_{kind}"]
            (state.hide() if self.report_views[kind].get_children() else state.show())
        if self.mark_view.get_children():
            states["marks"].hide()
        else:
            states["marks"].show()

    def refresh_all(self) -> None:
        if not self.workbench.flush():
            return
        self.workbench.refresh_directory()
        for view in (self.source_view, self.mark_view):
            view.delete(*view.get_children())
        for view in self.report_views.values():
            view.delete(*view.get_children())
        if not self.store:
            for view in (self.summary_view, self.detail_view, self.check_view):
                view.delete(*view.get_children())
            self.detail_tools.reset_selection()
            self.balance_tools.reset_selection()
            self.balance_calc_sources.clear()
            self._sync_empty_states()
            return
        self.period_options = {f"{year}年{period}期": (year, period) for year, period in self.store.periods()}
        for variable in (self.period_from, self.period_to):
            if variable.get() != "不限" and variable.get() not in self.period_options:
                variable.set("不限")
        balance_sources = [source for source in self.store.sources() if source["kind"] == "balance"]
        self.balance_options = {f'{source["id"]}: {source["filename"]} / {source["sheet"]}': source["id"] for source in balance_sources}
        self.balance_combo.configure(values=tuple(self.balance_options) if self.balance_options else ("无余额表",))
        if self.balance_source.get() not in self.balance_options:
            self.balance_source.set(next(reversed(self.balance_options)) if self.balance_options else "无余额表")
        self.refresh_summary()
        self._refresh_detail_accounts()
        self.refresh_details()
        for kind in REPORT_KINDS:
            report_sources = [source for source in self.store.sources() if source["kind"] == kind]
            self.report_options[kind] = {f'{source["id"]}: {source["filename"]} / {source["sheet"]}': source["id"] for source in report_sources}
            self.report_combos[kind].configure(values=tuple(self.report_options[kind]) if self.report_options[kind] else ("无报表",))
            if self.report_source[kind].get() not in self.report_options[kind]:
                self.report_source[kind].set(next(reversed(self.report_options[kind])) if self.report_options[kind] else "无报表")
            self.refresh_report(kind)
        self.refresh_checks()
        for row in self.store.sources():
            kind_label = {"entry": "明细", "balance": "科目余额", **REPORT_LABELS}.get(row["kind"], row["kind"])
            record = self.store.precheck_for_source(row["id"])
            precheck_label = record["verdict"] if record is not None else "无历史预检报告（旧版导入）"
            self.source_view.insert("", "end", iid=str(row["id"]), values=(row["filename"], row["sheet"], kind_label, row["row_count"], precheck_label, row["sha256"], row["original_path"]))
        self.refresh_marks()
        self._sync_empty_states()
        self.status.set(f"当前项目：{self.store.path.name}  ·  已导入 {len(self.store.sources())} 张表  ·  双击科目看明细，双击明细看凭证")

    def _read_task(self, key, operation, done):
        path = self.store.path
        def work(cancelled, report):
            store = LedgerStore.connect_existing(path, cancelled=cancelled)
            try:
                store.db.execute("BEGIN")
                return operation(store)
            finally:
                store.close()
        self.tasks.submit(key, work, done,
            lambda exc: self.status.set(f"读取失败，可重试：{exc}"))

    def refresh_summary(self):
        if not self.store:
            return
        selected_balance = self.balance_options.get(self.balance_source.get())
        search = self.summary_search.get().strip()
        self.balance_tools.reset_selection()
        self._summary_render_token = None
        self.tasks.cancel("summary")
        def render(found):
            self._render_summary(found)
        if self.store.large_book():
            self._read_task("summary", lambda store: store.summary(search, selected_balance), render)
        else:
            render(self.store.summary(search, selected_balance))

    def _render_summary(self, summary_rows):
        self.summary_view.delete(*self.summary_view.get_children())
        self.balance_calc_sources.clear()
        expansion_mode = self.store.setting("account_expansion_mode", "none")
        expanded = set(json.loads(self.store.setting("expanded_accounts", "[]"))) if expansion_mode == "custom" else set()
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
        chunk_size = len(summary_rows) if len(summary_rows) <= 1000 else 150
        self._summary_render_token = token = object()
        parents = set(parent_keys.values())
        def append(start=0):
            if not self.winfo_exists() or self._summary_render_token is not token:
                return
            for row in summary_rows[start:start + chunk_size]:
                # When no supplied balance table exists, show computed period movement only.
                origin = self.balance_source.get() if row["balance_count"] else "已导入明细汇总"
                self.balance_calc_sources[row["key"]] = f'{origin} · 科目 {row["code"]} {row["name"]}'
                pd = row["reported_debit"] if row["balance_count"] else row["detail_debit"]
                pc = row["reported_credit"] if row["balance_count"] else row["detail_credit"]
                side_tag = "debit_row" if row["closing_debit"] and not row["closing_credit"] else "credit_row" if row["closing_credit"] and not row["closing_debit"] else ""
                tags = tuple(filter(None, ("account_parent" if counts[row["key"]] > row["detail_count"] else "", side_tag)))
                self.summary_view.insert(parent_keys[row["key"]], "end", iid=row["key"], text=("− 折叠" if expansion_mode == "all" or row["key"] in expanded else "+ 展开") if row["key"] in parents else "", open=expansion_mode == "all" or row["key"] in expanded,
                                         values=(row["code"], row["name"], amount(row["opening_debit"]), amount(row["opening_credit"]), amount(pd), amount(pc), amount(row["year_debit"]), amount(row["year_credit"]), amount(row["closing_debit"]), amount(row["closing_credit"]), counts[row["key"]]), tags=tags)
            if start + chunk_size < len(summary_rows):
                self.after(1, append, start + chunk_size)
            else:
                self.summary_view.dashed_grid.schedule()
                self.balance_tools.structure_changed()
        append()

    def refresh_checks(self):
        if not self.store:
            return
        token = (self.store.setting("data_revision", "0"), tuple((row["id"], row["sha256"], row["row_count"], row["imported_at"]) for row in self.store.sources()),
                 self.store.setting("reconciliation_plan"))
        if self._checks_cache and self._checks_cache[0] == token:
            if not self.store.large_book() or self.utility_windows["核对提醒"].winfo_ismapped():
                self._render_checks(self._checks_cache[1])
            return
        def render(findings):
            self._checks_cache = (token, findings)
            if not self.store.large_book() or self.utility_windows["核对提醒"].winfo_ismapped():
                self._render_checks(findings)
        if self.store.large_book():
            self._read_task("checks", run_checks, render)
        else:
            render(run_checks(self.store))

    def _render_checks(self, findings):
        self.check_view.delete(*self.check_view.get_children())
        self._check_render_token = token = object()
        def append(start=0):
            if not self.winfo_exists() or self._check_render_token is not token:
                return
            for f in findings[start:start + 150]:
                tag = "difference" if f.level == "差异" else "pending" if f.level == "待核" else ""
                self.check_view.insert("", "end", values=(f.level, f.category, f.subject, f.detail), tags=(tag,) if tag else ())
            if start + 150 < len(findings):
                self.after(1, append, start + 150)
        append()

    def refresh_report(self, kind: str) -> None:
        view = self.report_views[kind]
        self.report_tools[kind].reset_selection()
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
        view.configure(displaycolumns=display)
        self.report_info[kind].set(f'{source["filename"]} · {source["sheet"]} · {source["row_count"]} 行 · 框选金额可合计及复制')
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

    @staticmethod
    def _flow_bar(container, first, second):
        if container.winfo_width() < first.winfo_reqwidth() + second.winfo_reqwidth() + 12:
            second.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        else:
            second.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)

    def _resize_detail_controls(self, event=None):
        width = self.detail_controls.winfo_width()
        if width <= 1:
            return
        wrap = max(120, min(340, width // 2))
        if int(self.detail_scope.cget('wraplength')) != wrap:
            self.detail_scope.configure(wraplength=wrap)
        self._flow_bar(self.detail_controls, self.detail_scope_wrap, self.detail_actions)

    def _detail_query_filters(self):
        period_start, period_end = self._period_bounds()
        conditions = tuple(self.detail_conditions[key] for key in sorted(self.detail_conditions))
        return dict(account=self.account, search=self.detail_search, conditions=conditions,
                    period_from=period_start, period_to=period_end, include_descendants=self.include_descendants,
                    sort_by=self.detail_sort, descending=self.detail_descending)

    def _resize_detail_status(self, event) -> None:
        wrap = max(80, event.width - 4)
        if int(self.detail_status.cget("wraplength")) != wrap:
            self.detail_status.configure(wraplength=wrap)

    def change_detail_page(self, direction):
        self.detail_offset = max(0, self.detail_offset + direction * 500)
        self.refresh_details()

    def refresh_details(self) -> None:
        self.detail_accounts.sync(self.account)
        self.tasks.cancel("details")
        self.detail_tools.reset_selection()
        self.detail_view.delete(*self.detail_view.get_children())
        self._update_detail_mark()
        self._update_detail_headings()
        if not self.store:
            return
        filters = self._detail_query_filters()
        scope = json.dumps(filters, sort_keys=True)
        if scope != getattr(self, "_detail_scope", None):
            self.detail_offset = 0
        self._detail_scope = scope
        self.detail_selection.set_scope(scope)
        offset = self.detail_offset
        locate_id = getattr(self, "detail_locate_target", None)
        self.detail_locate_target = None
        def query(store):
            current_offset = offset
            if locate_id is not None:
                current_offset = issue_store.entry_page_offset(store, locate_id)
            page = store.entries_page(**filters, offset=current_offset)
            marked = {row["voucher_key"] for row in store.db.execute("SELECT voucher_key FROM voucher_marks")}
            parties = build_party_index(store.related_parties())
            return page, marked, parties, locate_id
        self.detail_prev.configure(state="disabled")
        self.detail_next.configure(state="disabled")
        if self.store.large_book():
            self.detail_info.set("正在读取明细…")
            self._read_task("details", query, self._render_details)
        else:
            self._render_details(query(self.store))

    def _render_details(self, result):
        (rows_found, total, offset), marked_keys, party_index, *locate = result
        locate_id = locate[0] if locate else None
        self.detail_voucher_keys = {}
        self.detail_total, self.detail_offset = total, offset
        self.detail_view.delete(*self.detail_view.get_children())
        for row in rows_found:
            side_tag = "debit_row" if row["debit_cents"] and not row["credit_cents"] else "credit_row" if row["credit_cents"] and not row["debit_cents"] else ""
            self.detail_voucher_keys[str(row["id"])] = voucher_key(row)
            marked = voucher_key(row) in marked_keys
            self.detail_view.insert("", "end", iid=str(row["id"]), values=("●" if marked else "", row["entry_date"], row["fiscal_year"], row["period"], f'{row["voucher_type"]}{row["voucher_no"]}', row["code"], row["name"], row["summary"], row["counterparty"], indexed_party_match(row, party_index), row["auxiliary"], row["currency"], row["original_amount"], amount(row["debit_cents"]), amount(row["credit_cents"]), row["maker"], f'{row["filename"]}/{row["sheet"]}:{row["row_no"]}'), tags=("marked",) if marked else ((side_tag,) if side_tag else ()))
        self.detail_selection.refresh()
        if locate_id is not None and self.detail_view.exists(str(locate_id)):
            self.detail_view.selection_set(str(locate_id))
            self.detail_view.see(str(locate_id))
            self._update_detail_mark()
        pages = max(1, (total + 499) // 500)
        self.detail_page_label.set(f"第 {offset // 500 + 1} / {pages} 页")
        if pages > 1:
            self.detail_paging.pack(side="right", before=self.detail_status)
        else:
            self.detail_paging.pack_forget()
        self.detail_prev.configure(state="normal" if offset else "disabled")
        self.detail_next.configure(state="normal" if offset + 500 < total else "disabled")
        end = offset + len(rows_found)
        shown = f"显示 {offset + 1}—{end} 条" if rows_found else "暂无匹配明细"
        filter_count = len(self.detail_conditions) + bool(self.detail_search) + self._has_detail_filter("period")
        filter_info = f" · 已筛选 {filter_count} 项" if filter_count else " · 点击摘要、日期或金额列标题筛选"
        self.detail_info.set(f"共 {total:,} 条 · {shown}{filter_info} · {self._period_label()}")
        self._update_filter_chips()
        self._sync_empty_states()

    def _update_filter_chips(self):
        for child in self.detail_filter_bar.winfo_children():
            child.destroy()
        if not self.detail_conditions:
            self.detail_filter_bar.pack_forget()
            return
        from .detail_filter_ui import condition_label
        self.detail_filter_bar.pack(fill='x', after=self.detail_controls)
        for column, condition in self.detail_conditions.items():
            ttk.Button(self.detail_filter_bar, text=condition_label(condition) + ' ×', style='Ghost.TButton',
                       command=lambda key=column: self._set_detail_filter(key, None))
        ttk.Button(self.detail_filter_bar, text='清除全部', style='Quiet.TButton', command=self.clear_filter)
        self.after_idle(self._layout_filter_chips)

    def _layout_filter_chips(self, _event=None):
        width = max(100, self.detail_filter_bar.winfo_width() - 12)
        used = top = row_height = 0
        for child in self.detail_filter_bar.winfo_children():
            needed = min(width, child.winfo_reqwidth())
            height = child.winfo_reqheight()
            if used and used + needed > width:
                used, top, row_height = 0, top + row_height + 2, 0
            child.place(x=used, y=top, width=needed, height=height)
            used += needed + 6
            row_height = max(row_height, height)
        height = top + row_height + 4
        if int(self.detail_filter_bar.cget('height')) != height:
            self.detail_filter_bar.configure(height=height)

    def _update_detail_headings(self) -> None:
        for key, title, _ in VIEW_COLUMNS["detail"]:
            arrow = (" ↓" if self.detail_descending else " ↑") if key == self.detail_sort else ""
            image = self.detail_filter_images[self._has_detail_filter(key)] if key in DETAIL_FILTER_LABELS else ""
            condition = self.detail_conditions.get(key)
            if condition:
                if key == 'summary':
                    title += ' · ' + condition.values[0][:10]
                elif key == 'date':
                    title += f' · {len(condition.values)}个月'
                else:
                    title += ' · 已筛选'
            self.detail_view.heading(key, text=title + arrow, image=image)

    def open_account(self, _event: object) -> None:
        if _event is not None and self.summary_view.identify_column(_event.x) == '#0':
            return 'break'
        selection = self.summary_view.selection()
        if not selection:
            return
        self.account = selection[0]
        self.include_descendants = self.account.startswith("C:")
        self._reset_detail_filters()
        values = self.summary_view.item(selection[0], "values")
        self.account_label.set(f"{values[0]} {values[1]}".strip() + (" · 含子科目" if self.include_descendants else ""))
        self.refresh_details()
        self.select_page("entries")

    def toggle_detail_accounts(self, visible=None, *, persist=True):
        shown = str(self.detail_accounts) in self.detail_body.panes()
        visible = not shown if visible is None else visible
        if visible and not shown:
            self.detail_body.insert(0, self.detail_accounts, weight=0)
        elif not visible and shown:
            self.detail_body.forget(self.detail_accounts)
        self.detail_accounts_toggle.configure(text="隐藏科目树 ◂" if visible else "显示科目树 ▸")
        if persist and self.store:
            self.store.save_setting("detail_account_tree", "show" if visible else "hide")
        self.after_idle(self._resize_workspace)

    def _refresh_detail_accounts(self):
        if not self.store:
            self.detail_accounts.reset()
            return
        def read(store):
            return store.db.execute("SELECT key,code,name FROM accounts ORDER BY code,name").fetchall()
        if self.store.large_book():
            self._read_task("detail_accounts", read, self.detail_accounts.set_accounts)
        else:
            self.detail_accounts.set_accounts(read(self.store))

    def _choose_detail_account(self, account):
        self.account = account["key"] if account else None
        self.include_descendants = bool(self.account and self.account.startswith("C:"))
        label = f'{account["code"]} {account["name"]}'.strip() if account else "全部科目"
        self.account_label.set(label + (" · 含子科目" if self.include_descendants else ""))
        self.detail_offset = 0
        self.refresh_details()

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
                self.summary_view.item(item, text=('− 折叠' if self.summary_view.item(item, 'open') else '+ 展开') if children else '')
                visit(item)
        visit()
        self.summary_view.dashed_grid.schedule()
        self.balance_tools.structure_changed()

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
        if _event is not None and self.detail_view.identify_column(_event.x) == "#0":
            return "break"
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
                if old.winfo_exists() and not old.request_close():
                    return
            self.voucher_windows.clear()
        target = lines[0]
        existing_mark = mark_for_entry(self.store, entry_id)
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.ledger_app = self
        dialog.voucher_key = voucher_key(target)
        self.voucher_windows.add(dialog)
        dialog.bind("<Destroy>", lambda event: self.voucher_windows.discard(dialog) if event.widget is dialog else None)
        dialog.title(f'凭证 · {target["fiscal_year"]}年{target["period"]}期 {target["voucher_type"]}{target["voucher_no"]}')
        dialog.minsize(620, 240)
        voucher_top = ttk.Frame(dialog, padding=10)
        voucher_top.pack(fill="x")
        ttk.Label(voucher_top, text="已导入的凭证分录").pack(side="left")
        ttk.Label(voucher_top, text="按 ESC 键关闭凭证", style="Hint.TLabel").pack(side="left", padx=(12, 0))
        more = ttk.Menubutton(voucher_top, text="显示设置")
        more.pack(side="right")
        display_menu = tk.Menu(more, tearoff=False)
        display_menu.add_command(label="选择显示列…", command=lambda: self._open_columns(dialog, "voucher"))
        display_menu.add_command(label="还原默认格式", command=self.reset_voucher_format)
        more.configure(menu=display_menu)
        view = tree(dialog, VIEW_COLUMNS["voucher"])
        dialog.voucher_view = view
        self.shortcuts.attach(dialog)
        dialog.cell_tools = TableTools(view, {"debit", "credit", "original"})
        self.voucher_actions.attach(view, lambda _row: voucher_key(target))
        ttk.Button(voucher_top, text="加入问题…", command=lambda: self.workbench.choose_issue(voucher_key(target))).pack(side="right", padx=4)
        ttk.Button(voucher_top, text="计算器", command=dialog.cell_tools.toggle).pack(side="right", padx=4)
        view.configure(height=min(8, max(2, len(lines))))
        self._register_column_view("voucher", view)
        for key, width in self.store.voucher_widths().items():
            if key in view["columns"]:
                view.column(key, width=width)
        view.bind("<ButtonRelease-1>", lambda _e, widget=view: self._save_voucher_widths(widget), add="+")
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
        saved_state = (attention.get(), editor.get("1.0", "end-1c"))
        actions = ttk.Frame(note_area)
        actions.pack(fill="x", pady=(7, 0))
        feedback = tk.StringVar()
        ttk.Label(actions, textvariable=feedback, style="Hint.TLabel").pack(side="left")
        def note_saved():
            nonlocal saved_state
            current = issue_store.note_record(self.store, voucher_key(target))
            pending_attention = attention.get() != saved_state[0]
            if not pending_attention:
                attention.set(bool(current))
            for item in view.get_children():
                view.item(item, tags=("marked",) if current else ())
            self.update_mark_indicators(voucher_key(target))
            saved_state = (bool(current), editor.get("1.0", "end-1c"))

        note_draft = self.note_hub.bind(dialog, editor, voucher_key(target), feedback, on_saved=note_saved)
        dialog.note_draft = note_draft

        def save_attention() -> bool:
            nonlocal saved_state
            requested_attention = attention.get()
            if not note_draft.flush() or not self.note_hub.flush():
                return False
            try:
                current = mark_for_entry(self.store, entry_id)
                if requested_attention:
                    if current is None:
                        issue_store.write_note(self.store, voucher_key(target), None, editor.get("1.0", "end-1c"))
                    feedback.set("已保存至关注凭证")
                elif current:
                    remove_mark(self.store, voucher_key(target))
                    feedback.set("已取消关注；问题关联保留")
                self.note_hub.broadcast(voucher_key(target))
                note_saved()
            except Exception as exc:
                feedback.set(f"保存失败：{exc}")
                return False
            self.refresh_marks(select_key=voucher_key(target) if attention.get() else None)
            return True

        def request_close() -> bool:
            if not note_draft.flush():
                editor.focus_set()
                return False
            if attention.get() != saved_state[0] and not save_attention():
                return False
            dialog.destroy()
            return True

        dialog.request_close = request_close
        dialog.note_editor = editor
        dialog.protocol("WM_DELETE_WINDOW", request_close)
        ttk.Button(actions, text="保存关注与注释", command=save_attention, style="Accent.TButton").pack(side="right")
        ttk.Button(actions, text="重读已保存备注…", command=lambda: self.note_hub.reload(editor)).pack(side="right", padx=4)
        collapsed_label = "查看 / 编辑关注备注" if existing_mark else "关注这张凭证"
        note_height = 0
        def toggle_notes(initial=False) -> None:
            nonlocal note_height
            width, height = (760, compact_height) if initial else (dialog.winfo_width(), dialog.winfo_height())
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
        ttk.Button(quick_actions, text="关闭", command=request_close).pack(side="right")
        dialog.bind("<Escape>", lambda _event: request_close())
        dialog.update_idletasks()
        compact_height = min(440, max(240, dialog.winfo_reqheight()))
        if edit_attention:
            toggle_notes(initial=True)
        initial_height = min(dialog.winfo_screenheight() - 100, compact_height + note_height) if edit_attention else compact_height
        center_window(dialog, self, 760, initial_height)
        dialog.deiconify()
        if edit_attention:
            editor.focus_set()
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
        icon_path = Path(__file__).with_name('resources') / 'icon-beta.png'
        if icon_path.exists():
            self.app_icon = tk.PhotoImage(master=self, file=str(icon_path)).subsample(4, 4)
            self.iconphoto(True, self.app_icon)
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
        self.ai_provider = "deepseek"
        self.preferences.update(import_strategy="deepseek", ai_provider="deepseek")
        self.font_size = default_size
        self.set_font_size(saved_size, persist=False)
        self.withdraw()
        self.windows: set[App] = set()
        self.book_windows: dict[Path, App] = {}
        self.new_window()

    def offer_ai_setup(self):
        if not self.preferences.get('onboarding_seen') and self.windows:
            return self.show_usage_tour(next(iter(self.windows)))

    def show_usage_tour(self, window):
        from .usage_tour import UsageTour
        existing = getattr(self, '_usage_tour', None)
        if existing is not None and not existing.closed:
            existing.lift()
            return existing
        self._usage_tour = UsageTour(window)
        return self._usage_tour

    def import_book_folder(self, source: Path) -> Path:
        return self.book_folder if self.preferences.get('book_location_mode') == 'fixed' else source.expanduser().resolve().parent

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
        self.preferences["import_strategy"] = provider
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
    application = Application()
    application.after_idle(application.offer_ai_setup)
    application.mainloop()


if __name__ == "__main__":
    main()
