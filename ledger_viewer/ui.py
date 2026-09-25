"""Small Tk desktop interface; all work is local and offline."""

from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .checks import run_checks
from .importer import REPORT_KINDS, ImportSpec, format_money, guess_header, guess_mapping, rows, sheet_names, text_value
from .store import LedgerStore


FIELD_LABELS = {
    "date": "日期", "fiscal_year": "会计年度", "period": "期间", "voucher_no": "凭证号", "voucher_type": "凭证字", "account_code": "科目编码",
    "account_name": "科目名称", "summary": "摘要", "debit": "借方金额", "credit": "贷方金额",
    "counterparty": "往来单位", "auxiliary": "辅助核算", "opening_debit": "期初借方",
    "opening_credit": "期初贷方", "period_debit": "本期借方", "period_credit": "本期贷方",
    "closing_debit": "期末借方", "closing_credit": "期末贷方",
    "opening_balance": "期初余额", "closing_balance": "期末余额", "direction": "余额方向",
    "currency": "币别", "original_amount": "原币金额", "maker": "制单人",
    "left_item": "左侧项目", "left_amount_1": "左侧金额 1", "left_amount_2": "左侧金额 2",
    "right_item": "右侧项目", "right_amount_1": "右侧金额 1", "right_amount_2": "右侧金额 2",
}

REPORT_LABELS = {"balance_sheet": "资产负债表", "income_statement": "利润表"}

VIEW_COLUMNS = {
    "balance": [("code", "科目编码", 125), ("name", "科目名称", 200), ("od", "期初借方", 115), ("oc", "期初贷方", 115), ("pd", "本期借方", 115), ("pc", "本期贷方", 115), ("cd", "期末借方", 115), ("cc", "期末贷方", 115), ("n", "明细行数", 80)],
    "detail": [("date", "日期", 105), ("year", "年度", 70), ("period", "期间", 65), ("voucher", "凭证", 120), ("code", "科目编码", 110), ("name", "科目名称", 180), ("summary", "摘要", 250), ("counterparty", "往来单位", 145), ("aux", "核算维度", 145), ("currency", "币别", 80), ("original", "原币金额", 100), ("debit", "借方", 105), ("credit", "贷方", 105), ("maker", "制单人", 90), ("source", "来源行", 200)],
    "voucher": [("code", "科目编码", 90), ("name", "科目名称", 125), ("summary", "摘要", 190), ("aux", "核算维度", 140), ("currency", "币别", 75), ("original", "原币金额", 100), ("debit", "借方", 90), ("credit", "贷方", 90), ("source", "来源行", 135)],
}

DEFAULT_COLUMNS = {
    "balance": [key for key, _, _ in VIEW_COLUMNS["balance"]],
    "detail": ["date", "period", "voucher", "code", "name", "summary", "counterparty", "debit", "credit", "source"],
    "voucher": ["code", "name", "summary", "debit", "credit", "source"],
}


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
    view.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
    view.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    for key, label, width in columns:
        view.heading(key, text=label)
        view.column(key, width=width, minwidth=65, stretch=False, anchor="e" if key in ("od", "oc", "pd", "pc", "cd", "cc", "debit", "credit", "left_a", "left_b", "right_a", "right_b", "a", "b") else "w")
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
        self.title("当前账套设置")
        self.resizable(False, False)
        self.transient(parent)
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="凭证打开方式").pack(anchor="w")
        current = parent.store.setting("voucher_open_mode", "replace")
        self.mode = tk.StringVar(value=current if current in ("replace", "multiple") else "replace")
        for label, value in (("新凭证自动关闭前一张", "replace"), ("连续打开，保留已打开凭证", "multiple")):
            ttk.Radiobutton(body, text=label, variable=self.mode, value=value, command=self._save_mode).pack(anchor="w", pady=3)
        ttk.Separator(body).pack(fill="x", pady=12)
        ttk.Label(body, text="显示列").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(body)
        row.pack(fill="x")
        for kind, label in (("balance", "科目余额"), ("detail", "明细账"), ("voucher", "凭证分录")):
            ttk.Button(row, text=label, command=lambda key=kind: parent._open_columns(self, key)).pack(side="left", padx=(0, 6))
        ttk.Label(body, text="设置仅保存到当前账套。", foreground="#555").pack(anchor="w", pady=(14, 0))
        ttk.Button(body, text="完成", command=self.destroy).pack(anchor="e", pady=(12, 0))

    def _save_mode(self) -> None:
        self.parent.store.save_setting("voucher_open_mode", self.mode.get())


class ImportDialog(tk.Toplevel):
    def __init__(self, parent: "App", path: Path):
        super().__init__(parent)
        self.parent = parent
        self.path = path
        self.title("导入表格：确认列映射")
        self.geometry("960x780")
        self.transient(parent)
        self.grab_set()
        self.sheet = tk.StringVar(value=sheet_names(path)[0])
        self.kind = tk.StringVar(value="明细账／序时账")
        self.header = tk.StringVar(value="1")
        self.columns: list[str] = []
        self.vars: dict[str, tk.StringVar] = {}
        self._layout()
        self.refresh_preview()

    def _layout(self) -> None:
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text=self.path.name, font=("", 14, "bold")).pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(top)
        row.pack(fill="x")
        ttk.Label(row, text="工作表").pack(side="left")
        sheet = ttk.Combobox(row, textvariable=self.sheet, values=sheet_names(self.path), state="readonly", width=25)
        sheet.pack(side="left", padx=(6, 18))
        sheet.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        ttk.Label(row, text="表类型").pack(side="left")
        kind = ttk.Combobox(row, textvariable=self.kind, values=("明细账／序时账", "科目余额表", "资产负债表", "利润表"), state="readonly", width=16)
        kind.pack(side="left", padx=(6, 18))
        kind.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        ttk.Label(row, text="表头行号").pack(side="left")
        ttk.Entry(row, textvariable=self.header, width=5).pack(side="left", padx=6)
        ttk.Button(row, text="按行号读取表头", command=lambda: self.refresh_preview(manual=True)).pack(side="left", padx=5)
        ttk.Label(top, text="先核对原表预览，再确认每个字段对应的列；〔空〕是空白单元格，0 是原表零值。", foreground="#555").pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(self, height=8, wrap="none", state="disabled", font=("Menlo", 10))
        self.preview.pack(fill="x", padx=12, pady=8)
        self.map_frame = ttk.LabelFrame(self, text="列映射", padding=12)
        self.map_frame.pack(fill="both", expand=True, padx=12, pady=4)
        bottom = ttk.Frame(self, padding=12)
        bottom.pack(fill="x")
        self.import_note = ttk.Label(bottom, text="账簿空白/小计行会跳过；请确认借贷列不是余额列。", foreground="#995300")
        self.import_note.pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bottom, text="导入", command=self.import_now).pack(side="right", padx=4)

    def refresh_preview(self, manual: bool = False) -> None:
        try:
            if manual:
                row_no = int(self.header.get())
                if row_no < 1:
                    raise ValueError("表头行号必须大于 0")
                chosen = next((line for number, line in rows(self.path, self.sheet.get()) if number == row_no), None)
                if chosen is None:
                    raise ValueError("找不到指定的表头行")
                headers = [text_value(value) for value in chosen]
                guess = guess_mapping(chosen, self.kind_code())
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
                fields = ("left_item", "left_amount_1", "left_amount_2", "right_item", "right_amount_1", "right_amount_2") if kind_code == "balance_sheet" else ("left_item", "left_amount_1", "left_amount_2")
                self.import_note.configure(text="报表保留原行序、标题和小计；空白金额与零值分开保存，不做勾稽核对。")
            else:
                fields = ("date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_code", "account_name", "summary", "debit", "credit", "counterparty", "auxiliary", "currency", "original_amount", "maker") if kind_code == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "period_debit", "period_credit", "closing_debit", "closing_credit", "closing_balance", "direction")
                self.import_note.configure(text="账簿空白/小计行会跳过；请确认借贷列不是余额列。")
            for n, field in enumerate(fields):
                r, c = divmod(n, 2)
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
        tail = "请查看对应报表页。" if spec.kind in REPORT_KINDS else "请查看“核对结果”页。"
        messagebox.showinfo("导入完成", f"成功导入 {count} 行；{tail}", parent=self.parent)


class App(tk.Toplevel):
    def __init__(self, controller: "Application"):
        super().__init__(controller)
        self.controller = controller
        self.title(f"法律尽调账簿浏览器 · v{__version__}")
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
        self.status = tk.StringVar(value="新建或打开一个本地账套开始。")
        self.column_views: dict[str, list[ttk.Treeview]] = {kind: [] for kind in VIEW_COLUMNS}
        self.voucher_windows: set[tk.Toplevel] = set()
        self._layout()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _layout(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 7))
        toolbar.pack(fill="x")
        actions = ttk.Frame(toolbar)
        actions.pack(fill="x")
        ttk.Button(actions, text="新建账套", command=self.new_book).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="打开账套", command=self.open_book).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="导入 Excel/CSV", command=self.import_file).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="导出当前明细 CSV", command=self.export_csv).pack(side="left")
        ttk.Button(actions, text="设置", command=self.open_settings).pack(side="right")
        search_row = ttk.Frame(toolbar)
        search_row.pack(fill="x", pady=(6, 0))
        ttk.Label(search_row, text="搜索").pack(side="left", padx=(0, 6))
        entry = ttk.Entry(search_row, textvariable=self.search)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.refresh_all())
        ttk.Button(search_row, text="查找", command=self.refresh_all).pack(side="left", padx=6)
        ttk.Button(search_row, text="清空", command=self.clear_filter).pack(side="left")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=4)
        frames = [ttk.Frame(self.tabs) for _ in range(7)]
        for frame, title in zip(frames, ("科目余额", "明细账", "资产负债表", "利润表", "辅助核算", "核对结果", "来源文件")):
            self.tabs.add(frame, text=title)
        balance_top = ttk.Frame(frames[0], padding=5)
        balance_top.pack(fill="x")
        ttk.Label(balance_top, text="余额表来源").grid(row=0, column=0, sticky="w")
        self.balance_combo = ttk.Combobox(balance_top, textvariable=self.balance_source, values=("无余额表",), state="readonly", width=28)
        self.balance_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.balance_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())
        ttk.Label(balance_top, text="不同期间的期末余额不相加", foreground="#555").grid(row=0, column=2, sticky="w")
        balance_top.columnconfigure(1, weight=1)
        self.summary_view = tree(frames[0], VIEW_COLUMNS["balance"])
        self._register_column_view("balance", self.summary_view)
        self.summary_view.bind("<Double-1>", self.open_account)
        detail_top = ttk.Frame(frames[1], padding=5)
        detail_top.pack(fill="x")
        ttk.Label(detail_top, textvariable=self.account_label).pack(fill="x", anchor="w")
        detail_controls = ttk.Frame(detail_top)
        detail_controls.pack(fill="x", pady=(4, 0))
        ttk.Button(detail_controls, text="显示全部科目", command=self.show_all_entries).pack(side="right")
        self.period_combo = ttk.Combobox(detail_controls, textvariable=self.period_filter, values=("全部期间",), state="readonly", width=17)
        self.period_combo.pack(side="right", padx=(5, 18))
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._period_changed())
        ttk.Label(detail_controls, text="会计期间").pack(side="right")
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
            combo = ttk.Combobox(top, textvariable=self.report_source[kind], values=("无报表",), state="readonly", width=65)
            combo.pack(side="left", fill="x", expand=True, padx=8)
            self.report_combos[kind] = combo
            combo.bind("<<ComboboxSelected>>", lambda _e, report_kind=kind: self.refresh_report(report_kind))
            info = tk.StringVar(value="尚未导入报表。")
            self.report_info[kind] = info
            ttk.Label(frame, textvariable=info, padding=(8, 3), foreground="#555").pack(fill="x")
            columns = [("left_item", "项目", 230), ("left_a", "金额 1", 125), ("left_b", "金额 2", 125)]
            if kind == "balance_sheet":
                columns += [("right_item", "右侧项目", 230), ("right_a", "右侧金额 1", 125), ("right_b", "右侧金额 2", 125)]
            columns += [("source", "来源行", 260)]
            self.report_views[kind] = tree(frame, columns)
        self.aux_view = tree(frames[4], [("label", "辅助项/往来单位", 280), ("debit", "借方合计", 150), ("credit", "贷方合计", 150), ("n", "行数", 90)])
        self.check_view = tree(frames[5], [("level", "级别", 70), ("category", "类别", 130), ("subject", "对象", 250), ("detail", "说明", 650)])
        self.source_view = tree(frames[6], [("file", "文件", 220), ("sheet", "工作表", 130), ("kind", "类型", 85), ("rows", "有效行", 80), ("hash", "SHA-256", 220), ("path", "原始路径", 430)])
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(12, 6)).pack(fill="x")

    def _register_column_view(self, kind: str, view: ttk.Treeview) -> None:
        self.column_views[kind].append(view)
        saved = self.store.display_columns(kind) if self.store else None
        view.configure(displaycolumns=visible_columns(kind, saved))

    def _change_columns(self, kind: str, selected: list[str]) -> None:
        if self.store:
            self.store.save_display_columns(kind, selected)
        self.column_views[kind] = [view for view in self.column_views[kind] if view.winfo_exists()]
        for view in self.column_views[kind]:
            view.configure(displaycolumns=selected)

    def _open_columns(self, parent: tk.Widget, kind: str) -> None:
        if not self.store:
            return
        ColumnDialog(parent, kind, visible_columns(kind, self.store.display_columns(kind)), lambda selected: self._change_columns(kind, selected))

    def open_settings(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择账套", "请先新建或打开本地账套。", parent=self)
            return
        SettingsDialog(self)

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
        chosen = filedialog.asksaveasfilename(parent=self, title="新建本地账套", defaultextension=".sqlite", filetypes=[("SQLite 账套", "*.sqlite")])
        if chosen:
            if Path(chosen).exists() and Path(chosen).stat().st_size:
                messagebox.showwarning("文件已存在", "请选择新的文件名；打开已有账套请用“打开账套”。", parent=self)
                return
            self.controller.open_book(Path(chosen), self)

    def open_book(self) -> None:
        chosen = filedialog.askopenfilename(parent=self, title="打开本地账套", filetypes=[("SQLite 账套", "*.sqlite"), ("所有文件", "*")])
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
        self.title(f"法律尽调账簿浏览器 v{__version__} · {path.name}")
        self.refresh_all()

    def import_file(self) -> None:
        if not self.store:
            messagebox.showinfo("先选择账套", "请先新建或打开本地账套。", parent=self)
            return
        chosen = filedialog.askopenfilename(parent=self, title="选择企业账簿", filetypes=[("Excel/CSV", "*.xlsx *.xlsm *.csv"), ("所有文件", "*")])
        if chosen:
            try:
                ImportDialog(self, Path(chosen))
            except Exception as exc:
                messagebox.showerror("读取失败", str(exc), parent=self)

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
        for view in (self.summary_view, self.detail_view, self.aux_view, self.check_view, self.source_view):
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
            self.summary_view.insert("", "end", iid=row["key"], values=(row["code"], row["name"], amount(row["opening_debit"]), amount(row["opening_credit"]), amount(pd), amount(pc), amount(row["closing_debit"]), amount(row["closing_credit"]), row["detail_count"]))
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
            self.check_view.insert("", "end", values=(f.level, f.category, f.subject, f.detail))
        for row in self.store.sources():
            kind_label = {"entry": "明细", "balance": "科目余额", **REPORT_LABELS}.get(row["kind"], row["kind"])
            self.source_view.insert("", "end", values=(row["filename"], row["sheet"], kind_label, row["row_count"], row["sha256"], row["original_path"]))
        self.status.set(f"账套：{self.store.path}  ·  来源 {len(self.store.sources())} 份  ·  双击科目看明细，双击明细看凭证")

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
        display = ["left_item", "left_a"]
        if "left_amount_2" in mapping:
            display.append("left_b")
        if kind == "balance_sheet" and "right_item" in mapping:
            for column, field, fallback in (("right_item", "right_item", "右侧项目"), ("right_a", "right_amount_1", "右侧金额 1"), ("right_b", "right_amount_2", "右侧金额 2")):
                view.heading(column, text=label(field, fallback))
            display.extend(("right_item", "right_a"))
            if "right_amount_2" in mapping:
                display.append("right_b")
        display.append("source")
        view.configure(displaycolumns=display)
        self.report_info[kind].set(f'来源：{source["original_path"]}  ·  工作表：{source["sheet"]}  ·  共 {source["row_count"]} 行；空白金额留空，零值显示 0.00')
        for row in self.store.report_rows(source_id):
            values = [row["left_item"], report_amount(row["left_amount_1_cents"]), report_amount(row["left_amount_2_cents"])]
            if kind == "balance_sheet":
                values.extend((row["right_item"], report_amount(row["right_amount_1_cents"]), report_amount(row["right_amount_2_cents"])))
            values.append(f'{source["filename"]}/{source["sheet"]}:{row["row_no"]}')
            view.insert("", "end", values=values)

    def refresh_details(self) -> None:
        self.detail_view.delete(*self.detail_view.get_children())
        if not self.store:
            return
        year, period = self._selected_period()
        rows_found = self.store.entries(account=self.account, search=self.search.get().strip(), fiscal_year=year, period=period)
        for row in rows_found:
            self.detail_view.insert("", "end", iid=str(row["id"]), values=(row["entry_date"], row["fiscal_year"], row["period"], f'{row["voucher_type"]}{row["voucher_no"]}', row["code"], row["name"], row["summary"], row["counterparty"], row["auxiliary"], row["currency"], row["original_amount"], amount(row["debit_cents"]), amount(row["credit_cents"]), row["maker"], f'{row["filename"]}/{row["sheet"]}:{row["row_no"]}'))
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
        view = tree(dialog, VIEW_COLUMNS["voucher"])
        view.configure(height=min(9, max(2, len(lines))))
        self._register_column_view("voucher", view)
        for key, width in self.store.voucher_widths().items():
            if key in view["columns"]:
                view.column(key, width=width)
        view.bind("<ButtonRelease-1>", lambda _e, widget=view: self._save_voucher_widths(widget))
        for line in lines:
            view.insert("", "end", values=(line["code"], line["name"], line["summary"], line["auxiliary"], line["currency"], line["original_amount"], amount(line["debit_cents"]), amount(line["credit_cents"]), f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'))
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
        self.withdraw()
        self.windows: set[App] = set()
        self.book_windows: dict[Path, App] = {}
        self.new_window()

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
