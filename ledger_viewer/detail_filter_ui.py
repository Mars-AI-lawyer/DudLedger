"""Filter editor and heading affordances for the detail ledger."""

import tkinter as tk
from tkinter import ttk

from .background import BackgroundTasks
from .detail_filters import make_condition
from .store import LedgerStore


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
    return column if column in ('date', 'summary', 'debit', 'credit', 'original') else None


def month_label(month):
    year, number = month.split('-')
    return f'{year}年{int(number)}月'


def condition_label(condition):
    if condition.column == 'date' and condition.operator == 'months':
        labels = [month_label(value) for value in condition.values]
        text = '、'.join(labels[:3]) + (f' 等{len(labels)}个月' if len(labels) > 3 else '')
        return '月份：' + text
    if condition.column == 'summary':
        text = condition.values[0]
        return '摘要：' + (text[:16] + '…' if len(text) > 16 else text)
    name = {'debit': '借方', 'credit': '贷方', 'original': '原币金额', 'date': '日期'}.get(condition.column, condition.column)
    operation = {'gt': '>', 'ge': '≥', 'eq': '=', 'le': '≤', 'lt': '<', 'between': '介于', 'contains': '包含', 'excludes': '不包含'}[condition.operator]
    return name + ' ' + operation + ' ' + ' 至 '.join(condition.values)


class MonthSelection:
    """Keep cross-year choices while displaying one year's month grid."""
    def __init__(self, months, selected=()):
        self.months = tuple(sorted(set(months) | set(selected)))
        self.selected = set(selected)
        self.years = tuple(sorted({month[:4] for month in self.months}, reverse=True))

    def choose(self, month, selected):
        if month in self.months:
            self.selected.add(month) if selected else self.selected.discard(month)

    def choose_year(self, year, selected):
        months = {month for month in self.months if month.startswith(year + '-')}
        self.selected.update(months) if selected else self.selected.difference_update(months)

    def condition(self):
        return make_condition('date', 'months', *sorted(self.selected)) if self.selected else None


class DetailFilterDialog(tk.Toplevel):
    def __init__(self, app, labels: dict[str, str], column: str):
        super().__init__(app)
        self.withdraw()
        self.app, self.store = app, app.store
        self.tasks = BackgroundTasks(self)
        self.title('明细账 · 列筛选')
        self.transient(app)
        self.resizable(False, False)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.months = None
        self.labels = labels
        self.field_labels = {key: label + (' · 已筛选' if app._has_detail_filter(key) else '')
                             for key, label in labels.items()}
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        self.field = ttk.Combobox(body, values=tuple(self.field_labels.values()), state='readonly', width=34)
        self.field.pack(fill='x', pady=(0, 10))
        self.field.set(self.field_labels[column])
        self.field.bind('<<ComboboxSelected>>', self._field_changed)
        self.sorting = ttk.Frame(body)
        self.editor = ttk.Frame(body)
        self.editor.pack(fill='x')
        self.error = tk.StringVar(self)
        ttk.Label(body, textvariable=self.error, foreground='#A65036', wraplength=360).pack(anchor='w', pady=(6, 0))
        footer = ttk.Frame(body)
        footer.pack(fill='x', pady=(8, 0))
        self.clear = ttk.Button(footer, text='清除此列条件', command=self._clear)
        self.clear.pack(side='left')
        self.apply = ttk.Button(footer, text='应用', style='Accent.TButton', command=self._apply)
        self.apply.pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=6)
        self._field_changed()
        # Stay near the table and within the display, even for a narrow workspace.
        self.update_idletasks()
        x = max(0, min(app.detail_view.winfo_rootx() + 30, self.winfo_screenwidth() - self.winfo_reqwidth() - 20))
        y = max(0, min(app.detail_view.winfo_rooty() + 28, self.winfo_screenheight() - self.winfo_reqheight() - 50))
        self.geometry(f'+{x}+{y}')
        self.deiconify()
        self.grab_set()

    def _field_changed(self, _event=None):
        self.error.set('')
        self.column = tuple(self.field_labels)[self.field.current()]
        for child in (*self.editor.winfo_children(), *self.sorting.winfo_children()):
            child.destroy()
        self.sorting.pack_forget()
        self.clear.configure(state='normal' if self.app._has_detail_filter(self.column) else 'disabled')
        self.apply.configure(state='normal')
        condition = self.app.detail_conditions.get(self.column)
        if self.column != 'summary':
            self.sorting.pack(fill='x', before=self.editor, pady=(0, 12))
            first, last = ('日期从早到晚', '日期从晚到早') if self.column == 'date' else ('金额从小到大', '金额从大到小')
            ttk.Button(self.sorting, text='↑ ' + first, command=lambda: self._sort(False)).pack(side='left')
            ttk.Button(self.sorting, text='↓ ' + last, command=lambda: self._sort(True)).pack(side='left', padx=(6, 0))
        if self.column == 'date':
            selected = condition.values if condition and condition.operator == 'months' else ()
            self.selected_months = set(selected)
            self._show_months()
            return
        values = condition.values if condition else ('',)
        self.start = tk.StringVar(self, values[0])
        self.end = tk.StringVar(self, values[1] if len(values) > 1 else '')
        if self.column == 'summary':
            self.operation = 'contains'
            ttk.Label(self.editor, text='摘要包含关键词').pack(anchor='w', pady=(0, 4))
            entry = ttk.Entry(self.editor, textvariable=self.start, width=34)
            entry.pack(fill='x')
            entry.bind('<Return>', lambda _event: self._apply())
            entry.focus_set()
            return
        self.operators = dict(NUMBER_OPERATORS)
        self.operator = ttk.Combobox(self.editor, values=tuple(self.operators.values()), state='readonly')
        self.operator.set(self.operators[condition.operator if condition else 'gt'])
        self.operator.pack(fill='x', pady=(0, 6))
        self.operator.bind('<<ComboboxSelected>>', self._operator_changed)
        self.inputs = ttk.Frame(self.editor)
        self.inputs.pack(fill='x')
        self._operator_changed()

    def _operator_changed(self, _event=None):
        self.error.set('')
        for child in self.inputs.winfo_children():
            child.destroy()
        self.operation = tuple(self.operators)[self.operator.current()]
        ttk.Label(self.inputs, text='金额（原币）' if self.column == 'original' else '金额（元）', style='Hint.TLabel').pack(anchor='w')
        entry = ttk.Entry(self.inputs, textvariable=self.start)
        entry.pack(fill='x', pady=4)
        entry.bind('<Return>', lambda _event: self._apply())
        if self.operation == 'between':
            ttk.Label(self.inputs, text='至').pack(anchor='w')
            end_entry = ttk.Entry(self.inputs, textvariable=self.end)
            end_entry.pack(fill='x', pady=4)
            end_entry.bind('<Return>', lambda _event: self._apply())
        entry.focus_set()

    def _show_months(self):
        if self.months is None:
            self.apply.configure(state='disabled')
            self.month_hint = ttk.Label(self.editor, text='正在读取账簿月份…', style='Hint.TLabel')
            self.month_hint.pack(anchor='w')
            if self.tasks.busy('months'):
                return
            if self.store.large_book():
                path = self.store.path
                def read(cancelled, _report):
                    store = LedgerStore.connect_existing(path, cancelled=cancelled)
                    try:
                        return store.entry_months()
                    finally:
                        store.close()
                self.tasks.submit('months', read, self._months_ready, self._months_failed)
            else:
                self._months_ready(self.store.entry_months())
            return
        self.month_selection = MonthSelection(self.months, self.selected_months)
        if not self.month_selection.years:
            ttk.Label(self.editor, text='账套中没有可识别的凭证月份', style='Hint.TLabel').pack(anchor='w')
            self.apply.configure(state='disabled')
            return
        ttk.Label(self.editor, text='按月勾选，可连续选择多个月；不选则显示全部。', style='Hint.TLabel').pack(anchor='w')
        row = ttk.Frame(self.editor)
        row.pack(fill='x', pady=8)
        self.year = tk.StringVar(self, min(self.selected_months)[:4] if self.selected_months else self.month_selection.years[0])
        year_combo = ttk.Combobox(row, textvariable=self.year, values=self.month_selection.years, state='readonly', width=8)
        year_combo.pack(side='left')
        ttk.Label(row, text='年').pack(side='left', padx=4)
        year_combo.bind('<<ComboboxSelected>>', self._render_year)
        ttk.Button(row, text='全选本年', command=lambda: self._choose_year(True)).pack(side='left', padx=4)
        ttk.Button(row, text='清除本年', command=lambda: self._choose_year(False)).pack(side='left')
        self.month_grid = ttk.Frame(self.editor)
        self.month_grid.pack(fill='x')
        self.month_summary = tk.StringVar(self)
        ttk.Label(self.editor, textvariable=self.month_summary, style='Hint.TLabel', wraplength=360).pack(anchor='w', pady=(8, 0))
        self._render_year()
        self.apply.configure(state='normal')

    def _render_year(self, _event=None):
        for child in self.month_grid.winfo_children():
            child.destroy()
        self.month_vars = {}
        for number in range(1, 13):
            month = f'{self.year.get()}-{number:02}'
            variable = tk.BooleanVar(self, month in self.month_selection.selected)
            self.month_vars[month] = variable
            ttk.Checkbutton(self.month_grid, text=f'{number}月', variable=variable,
                state='normal' if month in self.month_selection.months else 'disabled',
                command=lambda value=month: self._choose_month(value)).grid(row=(number-1)//4, column=(number-1)%4, sticky='w', padx=8, pady=6)
        self._month_summary()

    def _choose_month(self, month):
        self.month_selection.choose(month, self.month_vars[month].get())
        self._month_summary()

    def _choose_year(self, selected):
        self.month_selection.choose_year(self.year.get(), selected)
        self._render_year()

    def _month_summary(self):
        months = sorted(self.month_selection.selected)
        labels = '、'.join(month_label(month) for month in months[:6])
        suffix = f' 等{len(months)}个月' if len(months) > 6 else ''
        self.month_summary.set('已选：' + labels + suffix if months else '全部月份（未设筛选）')

    def _months_ready(self, months):
        self.months = months
        if self.column == 'date':
            self.month_hint.destroy()
            self._show_months()

    def _months_failed(self, _error):
        if self.column == 'date':
            self.month_hint.configure(text='读取月份失败，请关闭后重试。')

    def _clear(self):
        self._commit(None)

    def _sort(self, descending):
        column = self.column
        self.destroy()
        if self.app.store is self.store:
            self.app.sort_details(column, descending=descending)

    def _apply(self):
        if self.column == 'date':
            if not hasattr(self, 'month_selection'):
                return
            self._commit(self.month_selection.condition())
            return
        if self.column == 'summary' and not self.start.get().strip():
            self._commit(None)
            return
        values = (self.start.get(), self.end.get()) if self.operation == 'between' else (self.start.get(),)
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
