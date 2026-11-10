"""Filter editor and heading affordances for the detail ledger."""

import tkinter as tk
from tkinter import ttk

from .background import BackgroundTasks
from .detail_filters import FILTER_COLUMNS, TEXT_COLUMNS, make_condition
from .store import LedgerStore


TEXT_OPERATORS = {"contains": "包含", "excludes": "不包含", "eq": "等于"}
NUMBER_OPERATORS = {"gt": "大于 >", "ge": "大于或等于 ≥", "eq": "等于 =",
                    "le": "小于或等于 ≤", "lt": "小于 <", "between": "介于（含两端）"}


def filter_arrow(view, active=False):
    image = tk.PhotoImage(master=view, width=18, height=10)
    for row in range(6):
        left, right = 3 + row, 14 - row
        if active or row == 0:
            image.put("#87523D" if active else "#77766F", to=(left, row + 2, right + 1, row + 3))
        else:
            for x in (left, right):
                image.put("#77766F", (x, row + 2))
    return image


def heading_filter_column(view, x: int, y: int) -> str | None:
    if view.identify_region(x, y) != "heading":
        return None
    display = view.identify_column(x)
    column = view.column(display, "id")
    if column not in FILTER_COLUMNS and column != "period":
        return None
    columns = tuple(view.cget("displaycolumns"))
    if columns == ("#all",):
        columns = tuple(view.cget("columns"))
    widths = [view.column(key, "width") for key in columns]
    tree_width = view.column("#0", "width") if "tree" in tuple(map(str, view.cget("show"))) else 0
    scroll = round(view.xview()[0] * (sum(widths) + tree_width))
    edge = tree_width + sum(widths[:int(display[1:])]) - scroll
    # The heading image sits inside the theme's 8 px right padding.
    # Native heading identification above keeps separator dragging out of this hit area.
    return column if edge - 26 <= x <= edge - 4 else None


class DetailFilterDialog(tk.Toplevel):
    def __init__(self, app, labels: dict[str, str], column: str):
        super().__init__(app)
        self.app = app
        self.store = app.store
        self.tasks = BackgroundTasks(self)
        self.title("明细账筛选条件")
        self.transient(app)
        self.resizable(False, False)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.months = None
        self.month_list = None
        self.labels = labels
        self.field_labels = {
            key: label + (" · 已筛选" if app._has_detail_filter(key) else "")
            for key, label in labels.items()
        }
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="筛选列").pack(anchor="w")
        self.field = ttk.Combobox(body, values=tuple(self.field_labels.values()), state="readonly", width=30)
        self.field.pack(fill="x", pady=(4, 10))
        self.field.set(self.field_labels[column])
        self.field.bind("<<ComboboxSelected>>", self._field_changed)
        self.editor = ttk.Frame(body)
        self.editor.pack(fill="x")
        self.error = tk.StringVar(self)
        ttk.Label(body, textvariable=self.error, foreground="#A65036", wraplength=340).pack(anchor="w", pady=(6, 0))
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(8, 0))
        self.clear = ttk.Button(footer, text="清除本列", command=self._clear)
        self.clear.pack(side="left")
        self.apply = ttk.Button(footer, text="应用", style="Accent.TButton", command=self._apply)
        self.apply.pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=6)
        self._field_changed()
        self.grab_set()

    def _field_changed(self, _event=None):
        self.error.set("")
        self.column = tuple(self.field_labels)[self.field.current()]
        for child in self.editor.winfo_children():
            child.destroy()
        self.month_list = None
        self.clear.configure(state="normal" if self.app._has_detail_filter(self.column) else "disabled")
        self.apply.configure(state="normal")
        if self.column == "period":
            self.start = tk.StringVar(self, self.app.period_from.get())
            self.end = tk.StringVar(self, self.app.period_to.get())
            periods = ("不限", *self.app.period_options)
            for label, variable in (("起始会计期间", self.start), ("结束会计期间", self.end)):
                ttk.Label(self.editor, text=label).pack(anchor="w")
                ttk.Combobox(self.editor, textvariable=variable, values=periods,
                             state="readonly", width=18).pack(fill="x", pady=(4, 6))
            return
        condition = self.app.detail_conditions.get(self.column)
        self.operators = dict(TEXT_OPERATORS if self.column in TEXT_COLUMNS else NUMBER_OPERATORS)
        if self.column == "date":
            self.operators["months"] = "按自然月多选"
        default = "contains" if self.column in TEXT_COLUMNS else "eq"
        self.operator = ttk.Combobox(self.editor, values=tuple(self.operators.values()), state="readonly")
        self.operator.set(self.operators[condition.operator if condition else default])
        self.operator.pack(fill="x", pady=(0, 6))
        self.operator.bind("<<ComboboxSelected>>", self._operator_changed)
        values = condition.values if condition and condition.operator != "months" else ("",)
        self.selected_months = set(condition.values) if condition and condition.operator == "months" else set()
        self.start = tk.StringVar(self, values[0])
        self.end = tk.StringVar(self, values[1] if len(values) > 1 else "")
        self.inputs = ttk.Frame(self.editor)
        self.inputs.pack(fill="x")
        self._operator_changed()

    def _operator_changed(self, _event=None):
        self.error.set("")
        if self.month_list is not None:
            self.selected_months = {self.month_list.get(index) for index in self.month_list.curselection()}
        for child in self.inputs.winfo_children():
            child.destroy()
        self.month_list = None
        self.operation = tuple(self.operators)[self.operator.current()]
        self.apply.configure(state="normal")
        if self.operation == "months":
            self._show_months()
            return
        if self.column == "date":
            ttk.Label(self.inputs, text="例如 2025-03-31", style="Hint.TLabel").pack(anchor="w")
        entry = ttk.Entry(self.inputs, textvariable=self.start)
        entry.pack(fill="x", pady=4)
        entry.bind("<Return>", lambda _event: self._apply())
        if self.operation == "between":
            ttk.Label(self.inputs, text="至").pack(anchor="w")
            end_entry = ttk.Entry(self.inputs, textvariable=self.end)
            end_entry.pack(fill="x", pady=4)
            end_entry.bind("<Return>", lambda _event: self._apply())
        entry.focus_set()

    def _show_months(self):
        if self.months is None:
            self.apply.configure(state="disabled")
            self.month_hint = ttk.Label(self.inputs, text="正在读取自然月…", style="Hint.TLabel")
            self.month_hint.pack(anchor="w")
            if self.tasks.busy("months"):
                return
            if self.store.large_book():
                path = self.store.path
                def read(cancelled, _report):
                    store = LedgerStore.connect_existing(path, cancelled=cancelled)
                    try:
                        return store.entry_months()
                    finally:
                        store.close()
                self.tasks.submit("months", read, self._months_ready, self._months_failed)
            else:
                self._months_ready(self.store.entry_months())
            return
        months = sorted(set(self.months) | self.selected_months)
        if not months:
            ttk.Label(self.inputs, text="账套中没有可识别日期", style="Hint.TLabel").pack(anchor="w")
            self.apply.configure(state="disabled")
            return
        ttk.Label(self.inputs, text="点击月份可选中或取消，支持多选。", style="Hint.TLabel").pack(anchor="w")
        holder = ttk.Frame(self.inputs)
        holder.pack(fill="x", pady=(4, 0))
        self.month_list = tk.Listbox(holder, height=min(8, len(months)), selectmode="multiple", exportselection=False)
        bar = ttk.Scrollbar(holder, command=self.month_list.yview)
        self.month_list.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.month_list.pack(side="left", fill="x", expand=True)
        self.month_list.insert("end", *months)
        for index, month in enumerate(months):
            if month in self.selected_months:
                self.month_list.selection_set(index)
        self.apply.configure(state="normal")

    def _months_ready(self, months):
        self.months = months
        if self.column == "date" and self.operation == "months":
            self.month_hint.destroy()
            self._show_months()

    def _months_failed(self, _error):
        if self.column == "date" and self.operation == "months":
            self.month_hint.configure(text="读取自然月失败，重新选择此方式可重试。")

    def _clear(self):
        self._commit(None)

    def _apply(self):
        if self.column == "period":
            self._commit((self.start.get(), self.end.get()))
            return
        if self.operation == "months":
            if self.month_list is None:
                return
            values = tuple(self.month_list.get(index) for index in self.month_list.curselection())
        elif self.operation == "between":
            values = (self.start.get(), self.end.get())
        else:
            values = (self.start.get(),)
        try:
            condition = make_condition(self.column, self.operation, *values)
        except ValueError as exc:
            self.error.set(str(exc))
            return
        self._commit(condition)

    def _commit(self, value):
        self.destroy()
        if self.app.store is self.store:
            self.app._set_detail_filter(self.column, value)
