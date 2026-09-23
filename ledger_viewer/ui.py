"""Small Tk desktop interface; all work is local and offline."""

from __future__ import annotations

import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .checks import run_checks
from .importer import ImportSpec, format_money, guess_header, guess_mapping, rows, sheet_names, text_value
from .store import LedgerStore


FIELD_LABELS = {
    "date": "日期", "fiscal_year": "会计年度", "period": "期间", "voucher_no": "凭证号", "voucher_type": "凭证字", "account_code": "科目编码",
    "account_name": "科目名称", "summary": "摘要", "debit": "借方金额", "credit": "贷方金额",
    "counterparty": "往来单位", "auxiliary": "辅助核算", "opening_debit": "期初借方",
    "opening_credit": "期初贷方", "period_debit": "本期借方", "period_credit": "本期贷方",
    "closing_debit": "期末借方", "closing_credit": "期末贷方",
    "opening_balance": "期初余额", "closing_balance": "期末余额", "direction": "余额方向",
    "currency": "币别", "original_amount": "原币金额", "maker": "制单人",
}


def amount(cents: int) -> str:
    return format_money(cents, comma=True) if cents else ""


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
        view.column(key, width=width, minwidth=65, anchor="e" if key in ("od", "oc", "pd", "pc", "cd", "cc", "debit", "credit") else "w")
    return view


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
        kind = ttk.Combobox(row, textvariable=self.kind, values=("明细账／序时账", "科目余额表"), state="readonly", width=16)
        kind.pack(side="left", padx=(6, 18))
        kind.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        ttk.Label(row, text="表头行号").pack(side="left")
        ttk.Entry(row, textvariable=self.header, width=5).pack(side="left", padx=6)
        ttk.Button(row, text="按行号读取表头", command=lambda: self.refresh_preview(manual=True)).pack(side="left", padx=5)
        ttk.Label(top, text="先核对原表预览，再确认每个字段对应的列。空白表示该字段不导入。", foreground="#555").pack(anchor="w", pady=(8, 0))
        self.preview = tk.Text(self, height=8, wrap="none", state="disabled", font=("Menlo", 10))
        self.preview.pack(fill="x", padx=12, pady=8)
        self.map_frame = ttk.LabelFrame(self, text="列映射", padding=12)
        self.map_frame.pack(fill="both", expand=True, padx=12, pady=4)
        bottom = ttk.Frame(self, padding=12)
        bottom.pack(fill="x")
        ttk.Label(bottom, text="空白/小计行会跳过；请确认借贷列不是余额列。", foreground="#995300").pack(side="left")
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
                self.preview.insert("end", f"{number:>4}  " + " | ".join(text_value(value)[:26] for value in line[:14]) + "\n")
                if number >= row_no + 5:
                    break
            self.preview.configure(state="disabled")
            for child in self.map_frame.winfo_children():
                child.destroy()
            self.vars.clear()
            fields = ("date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_code", "account_name", "summary", "debit", "credit", "counterparty", "auxiliary", "currency", "original_amount", "maker") if self.kind_code() == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "period_debit", "period_credit", "closing_debit", "closing_credit", "closing_balance", "direction")
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
        return "entry" if self.kind.get() == "明细账／序时账" else "balance"

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
        messagebox.showinfo("导入完成", f"成功导入 {count} 行；请查看“核对结果”页。", parent=self.parent)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"法律尽调账簿浏览器 · v{__version__}")
        self.geometry("1200x760")
        self.minsize(950, 600)
        self.store: LedgerStore | None = None
        self.account: str | None = None
        self.account_label = tk.StringVar(value="全部科目")
        self.period_filter = tk.StringVar(value="全部期间")
        self.period_options: dict[str, tuple[str, str]] = {}
        self.balance_source = tk.StringVar(value="无余额表")
        self.balance_options: dict[str, int] = {}
        self.search = tk.StringVar()
        self.status = tk.StringVar(value="新建或打开一个本地账套开始。")
        self._layout()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _layout(self) -> None:
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="新建账套", command=self.new_book).pack(side="left", padx=3)
        ttk.Button(toolbar, text="打开账套", command=self.open_book).pack(side="left", padx=3)
        ttk.Button(toolbar, text="导入 Excel/CSV", command=self.import_file).pack(side="left", padx=3)
        ttk.Button(toolbar, text="导出当前明细 CSV", command=self.export_csv).pack(side="left", padx=3)
        ttk.Label(toolbar, text="搜索").pack(side="left", padx=(22, 5))
        entry = ttk.Entry(toolbar, textvariable=self.search, width=27)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.refresh_all())
        ttk.Button(toolbar, text="查找", command=self.refresh_all).pack(side="left", padx=4)
        ttk.Button(toolbar, text="清空", command=self.clear_filter).pack(side="left")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=6)
        frames = [ttk.Frame(self.tabs) for _ in range(5)]
        for frame, title in zip(frames, ("科目余额", "明细账", "辅助核算", "核对结果", "来源文件")):
            self.tabs.add(frame, text=title)
        balance_top = ttk.Frame(frames[0], padding=5)
        balance_top.pack(fill="x")
        ttk.Label(balance_top, text="余额表来源").pack(side="left")
        self.balance_combo = ttk.Combobox(balance_top, textvariable=self.balance_source, values=("无余额表",), state="readonly", width=52)
        self.balance_combo.pack(side="left", padx=8)
        self.balance_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_all())
        ttk.Label(balance_top, text="不同期间的期末余额不相加", foreground="#555").pack(side="left")
        self.summary_view = tree(frames[0], [("code", "科目编码", 125), ("name", "科目名称", 200), ("od", "期初借方", 115), ("oc", "期初贷方", 115), ("pd", "本期借方", 115), ("pc", "本期贷方", 115), ("cd", "期末借方", 115), ("cc", "期末贷方", 115), ("n", "明细行数", 80)])
        self.summary_view.bind("<Double-1>", self.open_account)
        detail_top = ttk.Frame(frames[1], padding=5)
        detail_top.pack(fill="x")
        ttk.Label(detail_top, textvariable=self.account_label).pack(side="left")
        ttk.Button(detail_top, text="显示全部科目", command=self.show_all_entries).pack(side="right")
        self.period_combo = ttk.Combobox(detail_top, textvariable=self.period_filter, values=("全部期间",), state="readonly", width=17)
        self.period_combo.pack(side="right", padx=(5, 18))
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._period_changed())
        ttk.Label(detail_top, text="会计期间").pack(side="right")
        self.detail_view = tree(frames[1], [("date", "日期", 105), ("year", "年度", 70), ("period", "期间", 65), ("voucher", "凭证", 120), ("code", "科目编码", 110), ("name", "科目名称", 180), ("summary", "摘要", 250), ("counterparty", "往来单位", 145), ("aux", "核算维度", 145), ("currency", "币别", 80), ("original", "原币金额", 100), ("debit", "借方", 105), ("credit", "贷方", 105), ("maker", "制单人", 90), ("source", "来源行", 200)])
        self.detail_view.bind("<Double-1>", self.open_voucher)
        self.aux_view = tree(frames[2], [("label", "辅助项/往来单位", 280), ("debit", "借方合计", 150), ("credit", "贷方合计", 150), ("n", "行数", 90)])
        self.check_view = tree(frames[3], [("level", "级别", 70), ("category", "类别", 130), ("subject", "对象", 250), ("detail", "说明", 650)])
        self.source_view = tree(frames[4], [("file", "文件", 220), ("sheet", "工作表", 130), ("kind", "类型", 85), ("rows", "有效行", 80), ("hash", "SHA-256", 220), ("path", "原始路径", 430)])
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(12, 6)).pack(fill="x")

    def _close(self) -> None:
        if self.store:
            self.store.close()
        self.destroy()

    def new_book(self) -> None:
        chosen = filedialog.asksaveasfilename(parent=self, title="新建本地账套", defaultextension=".sqlite", filetypes=[("SQLite 账套", "*.sqlite")])
        if chosen:
            if Path(chosen).exists() and Path(chosen).stat().st_size:
                messagebox.showwarning("文件已存在", "请选择新的文件名；打开已有账套请用“打开账套”。", parent=self)
                return
            self._use_book(Path(chosen))

    def open_book(self) -> None:
        chosen = filedialog.askopenfilename(parent=self, title="打开本地账套", filetypes=[("SQLite 账套", "*.sqlite"), ("所有文件", "*")])
        if chosen:
            self._use_book(Path(chosen))

    def _use_book(self, path: Path) -> None:
        try:
            new_store = LedgerStore(path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)
            return
        if self.store:
            self.store.close()
        self.store = new_store
        self.account = None
        self.account_label.set("全部科目")
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
        for f in run_checks(self.store):
            self.check_view.insert("", "end", values=(f.level, f.category, f.subject, f.detail))
        for row in self.store.sources():
            self.source_view.insert("", "end", values=(row["filename"], row["sheet"], "明细" if row["kind"] == "entry" else "余额", row["row_count"], row["sha256"], row["original_path"]))
        self.status.set(f"账套：{self.store.path}  ·  来源 {len(self.store.sources())} 份  ·  双击科目看明细，双击明细看凭证")

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
        selection = self.detail_view.selection()
        if not selection:
            return
        lines = self.store.voucher_lines(int(selection[0]))
        if not lines:
            return
        target = lines[0]
        dialog = tk.Toplevel(self)
        dialog.title(f'凭证分录 · {target["fiscal_year"]}年{target["period"]}期 {target["voucher_type"]}{target["voucher_no"]}')
        dialog.geometry("900x500")
        ttk.Label(dialog, text="根据已导入明细重组的凭证分录；不是原始凭证扫描件。", padding=12, foreground="#995300").pack(anchor="w")
        view = tree(dialog, [("code", "科目编码", 110), ("name", "科目名称", 170), ("summary", "摘要", 230), ("aux", "核算维度", 160), ("currency", "币别", 75), ("original", "原币金额", 105), ("debit", "借方", 105), ("credit", "贷方", 105), ("source", "来源行", 180)])
        for line in lines:
            view.insert("", "end", values=(line["code"], line["name"], line["summary"], line["auxiliary"], line["currency"], line["original_amount"], amount(line["debit_cents"]), amount(line["credit_cents"]), f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'))
        debit = sum(line["debit_cents"] for line in lines)
        credit = sum(line["credit_cents"] for line in lines)
        ttk.Label(dialog, text=f"已导入分录：{len(lines)} 行   借方 {amount(debit)}   贷方 {amount(credit)}   差额 {amount(debit-credit) or '0.00'}", padding=12).pack(anchor="w")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
