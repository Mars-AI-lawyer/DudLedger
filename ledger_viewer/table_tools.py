"""Spreadsheet range selection, live totals, copying and local arithmetic."""

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import tkinter as tk
from tkinter import ttk


def cell_number(value: str) -> Decimal:
    raw = value.strip().replace(",", "").replace("￥", "").replace("¥", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise ValueError("请选择有数值的金额格") from None
    if not number.is_finite():
        raise ValueError("请选择有数值的金额格")
    return number


@dataclass(frozen=True)
class AmountSnapshot:
    row: str
    column: str
    heading: str
    text: str
    value: Decimal
    source: str


@dataclass(frozen=True)
class SelectionSnapshot:
    rows: tuple[str, ...]
    columns: tuple[str, ...]
    cells: tuple[AmountSnapshot, ...]


@dataclass(frozen=True)
class CalculationStep:
    sign: int
    value: Decimal
    label: str
    snapshot: SelectionSnapshot | None = None


def selection_name(index: int) -> str:
    """Use familiar A..Z, AA.. labels for committed ranges."""
    label = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return f"选区 {label}"


class Calculation:
    def __init__(self):
        self.items: list[CalculationStep] = []

    def add(self, sign: int, value: str, label: str, snapshot: SelectionSnapshot | None = None):
        if sign not in (-1, 1):
            raise ValueError("只支持加和减")
        self.items.append(CalculationStep(sign, cell_number(value), label, snapshot))

    @property
    def total(self) -> Decimal:
        # Preserve cents and longer original-currency decimals without floats.
        upper = max((step.value.adjusted() for step in self.items), default=0)
        lower = min((step.value.as_tuple().exponent for step in self.items), default=0)
        with localcontext() as context:
            context.prec = max(28, upper - lower + len(str(len(self.items))) + 3)
            return sum((step.value if step.sign == 1 else -step.value for step in self.items), Decimal(0))

    def undo(self):
        if self.items:
            self.items.pop()

    def clear(self):
        self.items.clear()


def display_number(number: Decimal) -> str:
    text = format(number, ",f")
    if "." not in text:
        return text + ".00"
    whole, fraction = text.split(".")
    return whole + "." + fraction.ljust(2, "0")


@dataclass
class SelectionSummary:
    cells: int = 0
    count: int = 0
    numbers: int = 0
    total: Decimal = Decimal(0)


class TableTools:
    """Track a rectangular range in displayed order, independently of row focus."""

    def __init__(self, view: ttk.Treeview, money_columns: set[str], *, source_for_row=None,
                 on_visibility_changed=None, panel_parent=None, on_toggle=None, show_stats=True):
        self.view = view
        view.cell_tools = self
        self.money_columns = money_columns
        self.selection_hint = "拖动框选，自动合计与计数；Ctrl+C / ⌘C 复制选区" if money_columns else "拖动框选；Ctrl+C / ⌘C 复制选区"
        self.source_for_row = source_for_row
        self.on_visibility_changed = on_visibility_changed
        self.on_toggle = on_toggle
        self.active: tuple[str, str] | None = None
        self.anchor: tuple[str, str] | None = None
        self.column_order = ()
        self.summary = SelectionSummary()
        self.summary_dirty = False
        self.dragging = False
        self.drag_point = (0, 0)
        self.scroll_job = None
        self.row_cache = {}
        self.calculation = Calculation()
        self._compact_width: int | None = None
        self._height_before_open = None
        self._grown_height = None
        self.job = None
        self.stats = tk.StringVar(value=self.selection_hint)
        if show_stats:
            ttk.Label(view.master, textvariable=self.stats, padding=(5, 4), anchor="w").grid(
                row=2, column=0, columnspan=2, sticky="ew")
        self.panel = ttk.Frame(panel_parent if panel_parent is not None else view.master, padding=(12, 8), width=270)
        self.panel.grid(row=0, column=2, rowspan=2, sticky="nsew")
        self.panel.grid_remove()
        self.panel.columnconfigure(0, weight=1)
        self.panel.rowconfigure(4, weight=1)
        top = ttk.Frame(self.panel)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="计算器", font=("", 12, "bold")).pack(side="left")
        ttk.Button(top, text="收起", width=4, command=self.toggle).pack(side="right")
        selection = ttk.Frame(self.panel)
        selection.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        ttk.Label(selection, text="当前选区", font=("", 11, "bold")).pack(anchor="w")
        self.selected = tk.StringVar(value="拖动框选一段金额")
        self.selection_label = ttk.Label(selection, textvariable=self.selected, wraplength=245, justify="left")
        self.selection_label.pack(anchor="w", pady=(3, 5))
        self.selection_total = tk.StringVar(value="—")
        self.selection_total_entry = ttk.Entry(selection, textvariable=self.selection_total,
                                                state="readonly", justify="right", width=24)
        self.selection_total_entry.pack(fill="x")
        for event in ("<Control-c>", "<Command-c>"):
            self.selection_total_entry.bind(event, lambda _event: self._copy_entry(self.selection_total_entry))
        operations = ttk.Frame(self.panel)
        operations.grid(row=2, column=0, sticky="ew")
        self.plus = ttk.Button(operations, text="＋ 加入", width=8, command=lambda: self.calculate(1), state="disabled")
        self.plus.pack(side="left", fill="x", expand=True)
        self.minus = ttk.Button(operations, text="－ 减去", width=8, command=lambda: self.calculate(-1), state="disabled")
        self.minus.pack(side="left", fill="x", expand=True, padx=(6, 0))
        history_title = ttk.Label(self.panel, text="计算过程", font=("", 11, "bold"))
        history_title.grid(row=3, column=0, sticky="w", pady=(12, 5))
        history_holder = ttk.Frame(self.panel)
        history_holder.grid(row=4, column=0, sticky="nsew")
        history_holder.rowconfigure(0, weight=1)
        history_holder.columnconfigure(0, weight=1)
        self.history = ttk.Treeview(history_holder, columns=("operation", "amount"), show="", height=3, selectmode="browse")
        self.history.column("operation", width=145, minwidth=120)
        self.history.column("amount", width=115, minwidth=95, anchor="e")
        scroll = ttk.Scrollbar(history_holder, command=self.history.yview)
        horizontal = ttk.Scrollbar(history_holder, orient="horizontal", command=self.history.xview)
        self.history.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.history.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.history.bind("<<TreeviewSelect>>", self._show_history_detail)
        self.history_detail = tk.Text(self.panel, height=2, width=28, wrap="word", state="disabled",
                                      relief="flat", borderwidth=0, font="TkTextFont", background="#FAF9F5")
        self.history_detail.grid(row=5, column=0, sticky="ew", pady=(5, 8))
        self._set_history_detail("点击加或减，将当前选区记入计算。")
        result_title = ttk.Label(self.panel, text="累计结果", font=("", 11, "bold"))
        result_title.grid(row=6, column=0, sticky="w")
        self.result = tk.StringVar(value="0.00")
        self.result_entry = ttk.Entry(self.panel, textvariable=self.result, state="readonly", justify="right", width=24)
        self.result_entry.grid(row=7, column=0, sticky="ew", pady=(3, 8))
        for event in ("<Control-c>", "<Command-c>"):
            self.result_entry.bind(event, self._copy_result_selection)
        actions = ttk.Frame(self.panel)
        actions.grid(row=8, column=0, sticky="ew")
        self.undo_button = ttk.Button(actions, text="撤销上一步", width=10, command=self.undo, state="disabled")
        self.undo_button.pack(side="left")
        self.clear_button = ttk.Button(actions, text="清空", width=6, command=self.clear, state="disabled")
        self.clear_button.pack(side="right")
        self.hint = ttk.Label(self.panel, text="换选区只更新预览，点击加减后累计。", foreground="#666", wraplength=245)
        self.hint.grid(row=9, column=0, sticky="w", pady=(8, 0))
        if panel_parent is not None:
            # Keep the result and every calculation action above the scrollable history.
            self.panel.rowconfigure(4, weight=0)
            self.panel.rowconfigure(7, weight=1)
            for widget, row in ((result_title, 3), (self.result_entry, 4), (actions, 5),
                                (history_title, 6), (history_holder, 7), (self.history_detail, 8)):
                widget.grid_configure(row=row)
        self.borders = [tk.Frame(view, background="#587A67", borderwidth=0) for _ in range(4)]
        for border in self.borders:
            border.bind("<Button-1>", self._click)
            border.bind("<B1-Motion>", self._drag)
            border.bind("<ButtonRelease-1>", self._release)
        self.menu = tk.Menu(view, tearoff=False)
        self.menu.add_command(label="复制选区", command=self.copy_cell)
        if money_columns:
            self.menu.add_separator()
            self.menu.add_command(label="加入计算", command=lambda: self.calculate(1, reveal=True))
            self.menu.add_command(label="从结果减去", command=lambda: self.calculate(-1, reveal=True))
        view.bind("<Button-1>", self._click, add="+")
        view.bind("<B1-Motion>", self._drag)
        view.bind("<ButtonRelease-1>", self._release, add="+")
        for event in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            view.bind(event, self._context, add="+")
        for event in ("<Command-c>", "<Control-c>", "<<Copy>>"):
            view.bind(event, self.copy_cell)
        for event in ("<Configure>", "<Map>", "<ButtonRelease-1>"):
            view.bind(event, self.schedule, add="+")
        for direction in ("Left", "Right", "Up", "Down"):
            view.bind(f"<{direction}>", lambda event, step=direction: self._move(step))
            view.bind(f"<Shift-{direction}>", lambda event, step=direction: self._move(step, extend=True))
        view.bind("<Destroy>", self._destroy, add="+")

    def columns(self):
        columns = self.view.cget("displaycolumns")
        if columns in ("#all", ("#all",)):
            columns = self.view.cget("columns")
        return self.view.tk.splitlist(columns) if isinstance(columns, str) else tuple(columns)

    def _coordinates(self, event):
        if getattr(event, "widget", self.view) is not self.view:
            return event.x_root - self.view.winfo_rootx(), event.y_root - self.view.winfo_rooty()
        return event.x, event.y

    def _locate(self, x, y, clamp=False):
        if clamp:
            x = max(2, min(self.view.winfo_width() - 3, x))
            y = max(2, min(self.view.winfo_height() - 3, y))
        row = self.view.identify_row(y)
        if not row and clamp:
            # Dragging through a heading or the blank space below the last row
            # extends to the nearest visible row, without selecting the heading.
            visible = [(point, self.view.identify_row(point))
                       for point in range(2, self.view.winfo_height() - 2, 4)]
            visible = [(point, item) for point, item in visible if item]
            if visible:
                row = min(visible, key=lambda pair: abs(pair[0] - y))[1]
        column = self.view.identify_column(x)
        columns = self.columns()
        index = int(column[1:]) - 1 if column and column != "#0" else -1
        if row and 0 <= index < len(columns):
            return row, columns[index]
        return None

    def _hit(self, event, extend=False):
        cell = self._locate(*self._coordinates(event))
        if cell:
            self.select(*cell, extend=extend)
            return True
        self.reset_selection()
        return False

    def _click(self, event):
        self._stop_scroll()
        if self.view.identify_region(*self._coordinates(event)) == "separator":
            self.dragging = False
            return None
        self.row_cache.clear()
        self.dragging = self._hit(event, extend=bool(getattr(event, "state", 0) & 1))
        if self.dragging:
            self.view.selection_set(self.active[0])
            self.view.focus(self.active[0])
            self.view.focus_set()
            return "break"

    def _drag(self, event):
        if not self.dragging:
            # Preserve Treeview's native heading-separator drag/resize handler.
            return None
        self.drag_point = self._coordinates(event)
        self._extend_drag()
        if self.scroll_job is None:
            self.scroll_job = self.view.after(70, self._autoscroll)
        return "break"

    def _extend_drag(self):
        cell = self._locate(*self.drag_point, clamp=True)
        if cell and cell != self.active:
            self.select(*cell, extend=True, refresh=False)

    def _autoscroll(self):
        self.scroll_job = None
        if not self.dragging:
            return
        x, y = self.drag_point
        width, height = self.view.winfo_width(), self.view.winfo_height()
        top = next((point for point in range(2, height, 4) if self.view.identify_row(point)), 30)
        vertical = -1 if y <= top + 3 else 1 if y >= height - 8 else 0
        horizontal = -1 if x <= 8 else 1 if x >= width - 8 else 0
        if vertical:
            self.view.yview_scroll(vertical, "units")
        if horizontal:
            self.view.xview_scroll(horizontal, "units")
        self._extend_drag()
        if vertical or horizontal:
            self.scroll_job = self.view.after(70, self._autoscroll)

    def _stop_scroll(self):
        if self.scroll_job is not None:
            self.view.after_cancel(self.scroll_job)
            self.scroll_job = None

    def _release(self, event):
        was_dragging = self.dragging
        if was_dragging:
            self.drag_point = self._coordinates(event)
            self._extend_drag()
        self.dragging = False
        self._stop_scroll()
        self._update_summary()
        return "break" if was_dragging else None

    def _context(self, event):
        cell = self._locate(*self._coordinates(event))
        selected_rows, selected_columns = self.selection_range()
        inside = cell and cell[0] in selected_rows and cell[1] in selected_columns
        if inside or self._hit(event):
            self.view.selection_set(self.active[0])
            self.view.focus(self.active[0])
            self.view.focus_set()
            try:
                self.menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu.grab_release()
        return "break"

    def select(self, row: str, column: str, *, extend=False, refresh=True):
        if not extend or self.anchor is None:
            self.anchor = (row, column)
            self.column_order = self.columns()
        self.active = (row, column)
        self.summary_dirty = True
        if refresh:
            self.row_cache.clear()
            self._update_summary()
        self.schedule()

    def selection_range(self):
        columns = self.columns()
        if not self.anchor or not self.active or columns != self.column_order:
            return (), ()
        rows = self.visible_rows()
        if any(cell[0] not in rows or cell[1] not in columns for cell in (self.anchor, self.active)):
            return (), ()
        start, end = sorted((rows.index(self.anchor[0]), rows.index(self.active[0])))
        left, right = sorted((columns.index(self.anchor[1]), columns.index(self.active[1])))
        return rows[start:end + 1], columns[left:right + 1]

    def visible_rows(self):
        """Use screen order, including expanded children and excluding closed ones."""
        if "tree" not in tuple(str(item) for item in self.view.tk.splitlist(self.view.cget("show"))):
            return self.view.get_children()
        result = []
        def visit(parent=""):
            for row in self.view.get_children(parent):
                result.append(row)
                if self.view.item(row, "open"):
                    visit(row)
        visit()
        return tuple(result)

    def _values(self, rows, columns):
        all_columns = tuple(self.view.cget("columns"))
        indices = [all_columns.index(column) for column in columns]
        for row in rows:
            if row not in self.row_cache:
                self.row_cache[row] = self.view.item(row, "values")
            values = self.row_cache[row]
            yield [str(values[index]) if index < len(values) else "" for index in indices]

    def _update_summary(self):
        rows, columns = self.selection_range()
        if not rows:
            self.reset_selection()
            return
        summary = SelectionSummary(cells=len(rows) * len(columns))
        numbers = Calculation()
        for values in self._values(rows, columns):
            for column, value in zip(columns, values):
                if value.strip():
                    summary.count += 1
                if column in self.money_columns:
                    try:
                        numbers.add(1, value, "")
                    except ValueError:
                        continue
                    summary.numbers += 1
        summary.total = numbers.total
        self.summary = summary
        self.summary_dirty = False
        stats = f"合计 {display_number(summary.total)}   计数 {summary.count}   金额 {summary.numbers} 项"
        self.stats.set(stats if self.money_columns else f"已选 {summary.cells} 格 · 可复制")
        self.selected.set(f"已选 {summary.numbers} 项金额 · 计数 {summary.count}")
        self.selection_total.set(display_number(summary.total) if summary.numbers else "—")
        if hasattr(self.view, "voucher_selection"):
            self.view.voucher_selection.changed()
        usable = summary.numbers > 0
        for button in (self.plus, self.minus):
            button.configure(state="normal" if usable else "disabled")
        for label in (("加入计算", "从结果减去") if self.money_columns else ()):
            self.menu.entryconfigure(label, state="normal" if usable else "disabled")

    def value(self):
        if not self.active or not self.view.exists(self.active[0]) or self.active[1] not in self.columns():
            return None
        return self.view.set(*self.active)

    def copy_cell(self, event=None):
        rows, columns = self.selection_range()
        if rows:
            self.row_cache.clear()
            values = list(self._values(rows, columns))
            if len(rows) == len(columns) == 1:
                self._copy(values[0][0])
            else:
                output = io.StringIO()
                csv.writer(output, delimiter="\t", lineterminator="\n").writerows(values)
                self._copy(output.getvalue().removesuffix("\n"))
        return "break"

    def _copy(self, value):
        self.view.clipboard_clear()
        self.view.clipboard_append(value)

    def _copy_result_selection(self, event):
        return self._copy_entry(self.result_entry)

    @staticmethod
    def _copy_entry(entry):
        entry.event_generate("<<Copy>>")
        return "break"

    def calculate(self, sign, reveal=False):
        # Read fresh values only at commit time, then retain an immutable copy.
        self.row_cache.clear()
        self._update_summary()
        rows, columns = self.selection_range()
        if not rows or not self.summary.numbers:
            return
        cells = []
        for row, values in zip(rows, self._values(rows, columns)):
            if self.source_for_row is not None:
                source = self.source_for_row(row)
            else:
                source = self.view.set(row, "source") if "source" in self.view.cget("columns") else row
            for column, value in zip(columns, values):
                if column not in self.money_columns:
                    continue
                try:
                    number = cell_number(value)
                except ValueError:
                    continue
                cells.append(AmountSnapshot(row, column, self.view.heading(column, "text"), value, number, source))
        snapshot = SelectionSnapshot(tuple(rows), tuple(columns), tuple(cells))
        self.calculation.add(sign, str(self.summary.total), selection_name(len(self.calculation.items) + 1), snapshot)
        if reveal and self.on_toggle is not None:
            self.on_toggle(True)
        elif reveal and not self.panel.winfo_manager():
            self.toggle()
        self._render()

    def _render(self):
        self.result.set(display_number(self.calculation.total))
        self.history.delete(*self.history.get_children())
        for index, step in enumerate(self.calculation.items):
            count = len(step.snapshot.cells) if step.snapshot else 1
            self.history.insert("", "end", iid=str(index), values=(
                f"{'＋' if step.sign == 1 else '－'} {step.label}（{count} 项）", display_number(step.value)))
        if self.calculation.items:
            last = str(len(self.calculation.items) - 1)
            self.history.selection_set(last)
            self.history.see(last)
            self._show_history_detail()
        else:
            self._set_history_detail("点击加或减，将当前选区记入计算。")
        for button in (self.undo_button, self.clear_button):
            button.configure(state="normal" if self.calculation.items else "disabled")

    def _set_history_detail(self, text):
        self.history_detail.configure(state="normal")
        self.history_detail.delete("1.0", "end")
        self.history_detail.insert("1.0", text)
        self.history_detail.configure(state="disabled")

    def _show_history_detail(self, event=None):
        selected = self.history.selection()
        if not selected or int(selected[0]) >= len(self.calculation.items):
            return
        step = self.calculation.items[int(selected[0])]
        lines = [f"{step.label} · 已固定的金额与来源"]
        if step.snapshot:
            lines.extend(f"{cell.source} · {cell.heading}：{cell.text}" for cell in step.snapshot.cells)
        self._set_history_detail("\n".join(lines))

    def undo(self):
        self.calculation.undo()
        self._render()

    def clear(self):
        self.calculation.clear()
        self._render()

    def set_compact_width(self, width: int | None) -> None:
        """Fit the calculator into the width reclaimed from the navigation sidebar."""
        if width == self._compact_width:
            return
        self._compact_width = width
        compact = width is not None
        self.panel.configure(padding=(8, 8) if compact else (12, 8))
        for label in (self.selection_label, self.hint):
            label.configure(wraplength=width - 20 if compact else 245)
        for entry in (self.selection_total_entry, self.result_entry):
            entry.configure(width=1 if compact else 24)
        self.history_detail.configure(width=1 if compact else 28)
        for button in (self.plus, self.minus):
            button.configure(width=0 if compact else 8)
        self.undo_button.configure(text="撤销" if compact else "撤销上一步", width=0 if compact else 10)
        self.clear_button.configure(width=0 if compact else 6)
        if compact:
            # Leave room for the history scrollbar, borders, and panel padding.
            history_width = width - 40
            operation_width = history_width // 2
            self.history.column("operation", width=operation_width, minwidth=24, stretch=False)
            self.history.column("amount", width=history_width - operation_width, minwidth=24, stretch=False)
        else:
            self.history.column("operation", width=145, minwidth=120, stretch=True)
            self.history.column("amount", width=115, minwidth=95, stretch=True)

    def toggle(self):
        if self.on_toggle is not None:
            return self.on_toggle(False)
        visible = not bool(self.panel.winfo_manager())
        if visible:
            self.panel.grid()
        else:
            self.panel.grid_remove()
        if self.on_visibility_changed is not None:
            self.on_visibility_changed(visible)
        owner = self.view.winfo_toplevel()
        if visible:
            owner.update_idletasks()
            desired = min(owner.winfo_reqheight(), owner.winfo_screenheight() - 100)
            if desired > owner.winfo_height():
                self._height_before_open = owner.winfo_height()
                self._grown_height = desired
                owner.geometry(f"{owner.winfo_width()}x{desired}")
        else:
            if self._grown_height is not None and abs(owner.winfo_height() - self._grown_height) <= 2:
                owner.geometry(f"{owner.winfo_width()}x{self._height_before_open}")
            self._height_before_open = self._grown_height = None
        self.schedule()

    def _move(self, direction, extend=False):
        if self.active and self.value() is not None:
            row, column = self.active
            columns = self.columns()
            if direction in ("Left", "Right"):
                index = max(0, min(len(columns) - 1, columns.index(column) + (-1 if direction == "Left" else 1)))
                column = columns[index]
            else:
                rows = self.visible_rows()
                if row not in rows:
                    self.reset_selection()
                    return "break"
                index = max(0, min(len(rows) - 1, rows.index(row) + (-1 if direction == "Up" else 1)))
                row = rows[index]
            self.view.see(row)
            self.view.selection_set(row)
            self.view.focus(row)
            self.select(row, column, extend=extend)
        return "break"

    def reset_selection(self):
        self._stop_scroll()
        self.dragging = False
        self.active = None
        self.anchor = None
        self.row_cache.clear()
        self.summary = SelectionSummary()
        self.summary_dirty = False
        self.stats.set(self.selection_hint)
        self.selected.set("拖动框选一段金额")
        self.selection_total.set("—")
        if hasattr(self.view, "voucher_selection"):
            self.view.voucher_selection.changed()
        for button in (self.plus, self.minus):
            button.configure(state="disabled")
        for border in self.borders:
            border.place_forget()
        for label in (("加入计算", "从结果减去") if self.money_columns else ()):
            self.menu.entryconfigure(label, state="disabled")

    def schedule(self, event=None):
        if self.job is None:
            self.job = self.view.after_idle(self._draw)

    def structure_changed(self):
        """Invalidate statistics when visible rows change, without taxing scrolling."""
        self.row_cache.clear()
        self.summary_dirty = True
        self.schedule()

    def _draw(self):
        self.job = None
        for border in self.borders:
            border.place_forget()
        if self.summary_dirty:
            self._update_summary()
        rows, columns = self.selection_range()
        if not rows:
            if self.anchor is not None:
                self.reset_selection()
            return
        selected_rows = set(rows)
        visible = []
        for point in range(1, self.view.winfo_height(), 4):
            row = self.view.identify_row(point)
            if row in selected_rows and (not visible or row != visible[-1]):
                visible.append(row)
        if not visible:
            return
        first_box = self.view.bbox(visible[0], columns[0])
        last_box = self.view.bbox(visible[0], columns[-1])
        bottom_box = self.view.bbox(visible[-1], columns[0])
        if not first_box or not last_box or not bottom_box:
            return
        x, y, width, _ = first_box
        width = last_box[0] + last_box[2] - x
        height = min(self.view.winfo_height() - 1, bottom_box[1] + bottom_box[3]) - y
        # Clip borders to the viewport, including partially scrolled cells.
        right = min(self.view.winfo_width() - 1, x + width)
        x = max(1, x)
        width = right - x
        if width <= 0:
            return
        for border, geometry in zip(self.borders, (
            dict(x=x, y=y, width=width, height=2),
            dict(x=x, y=y + height - 2, width=width, height=2),
            dict(x=x, y=y, width=2, height=height),
            dict(x=right - 2, y=y, width=2, height=height),
        )):
            border.place(**geometry)
            border.lift()

    def _destroy(self, event):
        if event.widget is self.view:
            self._stop_scroll()
            if self.job is not None:
                self.view.after_cancel(self.job)
                self.job = None
