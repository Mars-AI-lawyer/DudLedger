"""Dedicated tree-column drag handles, isolated from cell selection gestures."""

import tkinter as tk
from tkinter import ttk

from .workbench_support import VoucherDragSession, drag_handle_at


HANDLE_MARK = '⋮⋮'


class VoucherDragBinding:
    """Bind one voucher table to the workbench's single explicit-add path."""

    def __init__(self, view, key_for_row, selected_keys, resolve, commit, clear_target):
        self.view = view
        self.key_for_row = key_for_row
        self.selected_keys = selected_keys
        self.clear_target = clear_target
        self.tip = None
        self.tag = f'VoucherDrag:{id(self)}'
        view.bindtags((self.tag, *view.bindtags()))
        view.bind_class(self.tag, '<ButtonPress-1>', self.press)
        view.bind_class(self.tag, '<B1-Motion>', self.motion)
        view.bind_class(self.tag, '<ButtonRelease-1>', self.release)
        view.bind_class(self.tag, '<Escape>', self.escape)
        view.bind_class(self.tag, '<Destroy>', self.destroy)
        selection = getattr(view, 'voucher_selection', None)
        if selection is not None:
            refresh = selection.refresh
            def decorated_refresh():
                refresh()
                self.decorate()
            selection.refresh = decorated_refresh
        self.decorate()
        self.session = VoucherDragSession(resolve, commit, self.present, self.finish)

    def decorate(self):
        """Keep a visible handle beside the checkbox after selection refreshes."""
        self.view.column('#0', width=max(58, self.view.column('#0', 'width')), minwidth=58, stretch=False)
        heading = self.view.heading('#0', 'text').replace(f' {HANDLE_MARK}', '')
        self.view.heading('#0', text=f'{heading} {HANDLE_MARK}')
        for row in self.view.get_children():
            text = self.view.item(row, 'text').replace(f' {HANDLE_MARK}', '')
            self.view.item(row, text=f'{text} {HANDLE_MARK}')

    def _keys(self, row):
        key = self.key_for_row(row)
        selection = getattr(self.view, 'voucher_selection', None)
        selected = self.selected_keys()
        if selection is not None and selection.checked:
            return selected
        return selected if key in selected else (key,)

    def press(self, event):
        if self.session.active:
            return 'break'
        if not drag_handle_at(self.view.identify_column(event.x), event.x,
                              self.view.identify_region(event.x, event.y)):
            return None
        row = self.view.identify_row(event.y)
        if not row:
            return None
        self.view.focus_set()
        self.session.start(self._keys(row), event.x_root, event.y_root)
        return 'break'

    def motion(self, event):
        if not self.session.active:
            return None
        self.session.move(event.x_root, event.y_root)
        return 'break'

    def release(self, event):
        if not self.session.active:
            return None
        self.session.drop(event.x_root, event.y_root)
        return 'break'

    def escape(self, _event):
        return 'break' if self.session.cancel() else None

    def present(self, keys, target, x_root, y_root):
        if self.tip is None:
            self.tip = tk.Toplevel(self.view)
            self.tip.overrideredirect(True)
            self.tip.label = ttk.Label(self.tip, padding=8, relief='solid', justify='left')
            self.tip.label.pack()
        text = f'将归集 {len(keys)} 张凭证 · 会同步加入关注'
        if target:
            text += (f'\n目标：{target["title"]}\n新增 {target["added"]} 张 · '
                     f'已有重复 {target["duplicates"]} 张')
        else:
            text += '\n拖到已展开工作台中的问题；Esc 取消'
        self.tip.label.configure(text=text)
        self.tip.geometry(f'+{x_root + 14}+{y_root + 18}')

    def finish(self, committed):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self.clear_target(restore=not committed)

    def destroy(self, event):
        if event.widget is self.view:
            self.session.cancel()
