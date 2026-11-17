"""Voucher-level checks coexist with disjoint cell selection."""

import tkinter as tk
from tkinter import ttk


class VoucherSelection:
    def __init__(self, view, key_for_row, on_change=None):
        self.view, self.key_for_row, self.on_change = view, key_for_row, on_change
        self.checked = set()
        self.anchor = None
        self.scope = None
        view.voucher_selection = self
        self.tag = f'VoucherSelection:{id(self)}'
        view.bindtags((self.tag, *view.bindtags()))
        view.bind_class(self.tag, '<ButtonPress-1>', self.press)
        view.configure(show='tree headings')
        view.column('#0', width=36, minwidth=36, stretch=False)
        view.heading('#0', text='☐', command=self.toggle_all)
        view.bind('<<TreeviewSelect>>', lambda _e: self.changed(), add='+')
        self.info = tk.StringVar(value='')
        self.display_info = tk.StringVar(value='')
        self.context = ''
        self.feedback = ''

    def row_keys(self, rows):
        keys = []
        for row in rows:
            try:
                key = self.key_for_row(row)
            except (KeyError, TypeError, ValueError):
                continue
            if key and key not in keys:
                keys.append(key)
        return tuple(keys)

    def action_keys(self):
        if self.checked:
            return tuple(sorted(self.checked))
        tools = getattr(self.view, 'cell_tools', None)
        rows = tools.selection_range()[0] if tools else ()
        return self.row_keys(rows or self.view.selection())

    def set_scope(self, scope):
        if scope != self.scope:
            self.scope = scope
            self.checked.clear()
            self.anchor = None
        self.refresh()

    def clear(self):
        self.checked.clear()
        self.refresh()

    def refresh(self):
        visible = set(self.row_keys(self.view.get_children()))
        for row in self.view.get_children():
            self.view.item(row, text=('☑' if self.key_for_row(row) in self.checked else '☐'))
        self.view.heading('#0', text=('☑' if visible and visible <= self.checked else '☐'))
        self.changed()

    def changed(self):
        visible = set(self.row_keys(self.view.get_children()))
        if self.checked:
            outside = len(self.checked - visible)
            suffix = f'（含跨页 {outside} 张）' if outside else ''
            self.info.set(f'已勾选 {len(self.checked)} 张凭证{suffix} · 后续操作使用勾选范围')
        else:
            keys = self.action_keys()
            self.info.set(f'当前框选涉及 {len(keys)} 张凭证 · 尚未勾选' if keys else '')
        base = self.info.get() or self.context
        if self.feedback:
            base = (base + ' · ' if base else '') + self.feedback
        self.display_info.set(base)
        if self.on_change:
            self.on_change()

    def set_context(self, text=''):
        self.context = text
        self.changed()

    def show_feedback(self, text):
        self.feedback = text
        self.changed()

    def toggle_all(self):
        visible = set(self.row_keys(self.view.get_children()))
        if visible and visible <= self.checked:
            self.checked.difference_update(visible)
        else:
            self.checked.update(visible)
        self.refresh()

    def press(self, event):
        view = self.view
        if view.identify_column(event.x) != '#0' or view.identify_region(event.x, event.y) == 'separator':
            return
        row = view.identify_row(event.y)
        if not row:
            return
        rows = view.get_children()
        key = self.key_for_row(row)
        select = key not in self.checked
        affected = (row,)
        if event.state & 1 and self.anchor in rows:
            lo, hi = sorted((rows.index(self.anchor), rows.index(row)))
            affected = rows[lo:hi + 1]
        keys = self.row_keys(affected)
        self.checked.update(keys) if select else self.checked.difference_update(keys)
        self.anchor = row
        view.selection_set(row)
        view.focus(row)
        view.focus_set()
        self.refresh()
        return 'break'


def selection_bar(parent, selection):
    frame = ttk.Frame(parent, padding=(6, 2))
    frame.pack(side='bottom', fill='x', before=selection.view.master)
    label = ttk.Label(frame, textvariable=selection.display_info, style='Hint.TLabel', wraplength=300)
    label.grid(row=0, column=0, sticky='w')
    actions = ttk.Frame(frame)
    actions.grid(row=0, column=1, sticky='e')
    frame.columnconfigure(0, weight=1)
    ttk.Button(actions, text='清空勾选', command=selection.clear).pack(side='right')
    ttk.Button(actions, text='本页全选', command=selection.toggle_all).pack(side='right', padx=4)
    def layout(_event=None):
        # Keep one adaptive status line; narrow windows hide secondary select-all
        # instead of growing an additional toolbar row.
        compact = frame.winfo_width() < label.winfo_reqwidth() + actions.winfo_reqwidth() + 20
        select_all = getattr(frame, 'select_all_button', None)
        if select_all is not None:
            if compact:
                select_all.pack_forget()
            elif not select_all.winfo_manager():
                select_all.pack(side='right', padx=4, before=frame.clear_button)
        reserved = actions.winfo_reqwidth() + sum(
            child.winfo_reqwidth() for child in frame.winfo_children()
            if child not in (label, actions) and child.winfo_manager())
        label.configure(wraplength=max(80, frame.winfo_width() - reserved - 24))
    frame.select_all_button = next(child for child in actions.winfo_children() if child.cget('text') == '本页全选')
    frame.clear_button = next(child for child in actions.winfo_children() if child.cget('text') == '清空勾选')
    frame.bind('<Configure>', layout)
    label.bind('<Configure>', layout)
    return frame


class CellTooltip:
    """Show full cell text, including content clipped by a narrow column."""
    def __init__(self, view):
        self.view, self.job, self.tip, self.cell = view, None, None, None
        view.bind('<Motion>', self.motion, add='+')
        for event in ('<Leave>', '<ButtonPress-1>', '<Destroy>', '<MouseWheel>'):
            view.bind(event, self.hide, add='+')

    def motion(self, event):
        cell = (self.view.identify_row(event.y), self.view.identify_column(event.x))
        if cell == self.cell:
            return
        self.hide()
        self.cell = cell
        if cell[0] and cell[1] not in ('', '#0'):
            self.job = self.view.after(650, lambda: self.show(cell, event.x_root, event.y_root))

    def show(self, cell, x, y):
        self.job = None
        if not self.view.exists(cell[0]):
            return
        text = self.view.set(*cell)
        if not text:
            return
        self.tip = tk.Toplevel(self.view)
        self.tip.overrideredirect(True)
        ttk.Label(self.tip, text=text, padding=8, wraplength=560, relief='solid').pack()
        self.tip.geometry(f'{x + 12:+d}{y + 18:+d}')

    def hide(self, _event=None):
        if self.job:
            self.view.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None
        self.cell = None
