"""Issue workbench, local autosave, and handle-only Tk voucher dragging."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import issue_store as data
from .importer import format_money
from .voucher_notes import voucher_key


class Draft:
    """Debounced save that retains input on failure; callers must flush before leaving."""

    def __init__(self, owner, capture, save, status):
        self.owner, self.capture, self.save, self.status = owner, capture, save, status
        self.job = None
        self.loading = False
        self.disposed = False
        self.bindings = []
        self.baseline = capture()
        token = owner.bind("<Destroy>", self._destroy, add="+")
        self.bindings.append((owner, "<Destroy>", token))

    @property
    def dirty(self):
        return not self.disposed and self.capture() != self.baseline

    def changed(self, *_):
        if self.loading or self.disposed:
            return
        if self.job:
            self.owner.after_cancel(self.job)
        self.status.set("保存中…" if self.dirty else "已保存")
        self.job = self.owner.after(800, self.flush)

    def watch(self, widget):
        if isinstance(widget, tk.Text):
            widget.edit_modified(False)
            def modified(_event):
                if widget.edit_modified():
                    widget.edit_modified(False)
                    self.changed()
            token = widget.bind("<<Modified>>", modified, add="+")
            self.bindings.append((widget, "<<Modified>>", token))
        token = widget.bind("<FocusOut>", lambda _event: self.flush(), add="+")
        self.bindings.append((widget, "<FocusOut>", token))

    def flush(self):
        if self.job:
            self.owner.after_cancel(self.job)
            self.job = None
        if not self.dirty:
            return True
        value = self.capture()
        try:
            self.save(value)
        except Exception as exc:
            self.status.set(f"保存失败：{exc}")
            return False
        self.baseline = value
        self.status.set("已保存")
        return True

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        if self.job:
            self.owner.after_cancel(self.job)
            self.job = None
        for widget, sequence, token in self.bindings:
            try:
                if widget.winfo_exists():
                    widget.unbind(sequence, token)
            except tk.TclError:
                pass
        self.bindings.clear()

    def _destroy(self, event):
        if event.widget is self.owner:
            self.dispose()


class NoteHub:
    """Synchronize clean editors; conflicting dirty text is never overwritten."""

    def __init__(self, app):
        self.app = app
        self.items = {}

    def bind(self, owner, editor, key, status, on_saved=None):
        self.unbind(editor)
        store = self.app.store
        row = data.note_record(store, key)
        baseline = row["note"] if row else None
        editor.delete("1.0", "end")
        editor.insert("1.0", baseline or "")
        binding = {"key": key, "store": store, "baseline": baseline, "editor": editor, "on_saved": on_saved}
        def save(value):
            data.write_note(store, key, binding["baseline"], value)
            binding["baseline"] = value
            self.broadcast(key, source=id(binding))
            if on_saved:
                on_saved()
        draft = Draft(owner, lambda: editor.get("1.0", "end-1c"), save, status)
        binding["draft"] = draft
        self.items[id(binding)] = binding
        draft.watch(editor)
        status.set("已保存" if baseline is not None else "未关注；输入备注将自动关注")
        def destroyed(event):
            if event.widget is owner:
                self.items.pop(id(binding), None)
        binding["destroy_binding"] = owner.bind("<Destroy>", destroyed, add="+")
        return draft

    def unbind(self, editor):
        for token, item in list(self.items.items()):
            if item["editor"] is editor:
                draft = item["draft"]
                draft.dispose()
                if draft.owner.winfo_exists():
                    draft.owner.unbind("<Destroy>", item["destroy_binding"])
                self.items.pop(token, None)

    def reload(self, editor):
        item = next((item for item in self.items.values() if item["editor"] is editor), None)
        if item is None:
            return False
        if not messagebox.askyesno("读取已保存备注", "将当前输入复制到剪贴板，再读取数据库中的最新备注？\n可粘贴当前输入后继续核对。", parent=editor.winfo_toplevel()):
            return False
        editor.clipboard_clear()
        editor.clipboard_append(editor.get("1.0", "end-1c"))
        item["draft"].baseline = item["draft"].capture()
        self.broadcast(item["key"])
        return True

    def broadcast(self, key, source=None):
        if not self.app.store:
            return
        row = data.note_record(self.app.store, key)
        value = row["note"] if row else None
        for token, item in list(self.items.items()):
            draft = item["draft"]
            if token == source or item["key"] != key or item["store"] is not self.app.store or draft.dirty:
                continue
            draft.loading = True
            item["editor"].delete("1.0", "end")
            item["editor"].insert("1.0", value or "")
            item["editor"].edit_modified(False)
            item["baseline"] = value
            draft.baseline = value or ""
            draft.loading = False
            draft.status.set("已保存" if value is not None else "未关注；输入备注将自动关注")
            if item["on_saved"]:
                item["on_saved"]()

    def flush(self):
        for item in list(self.items.values()):
            if item["store"] is self.app.store and not item["draft"].flush():
                item["editor"].focus_set()
                self.app.status.set(item["draft"].status.get())
                return False
        return True


class RightWorkbench(ttk.Frame):
    """One issue editor, rebuilt between docked and detached hosts after flushing."""

    def __init__(self, app):
        super().__init__(app.content)
        self.app = app
        self.visible = False
        self.mode = 'issues'
        self.width = 480
        self.active_tools = self.last_tools = None
        self.current = self.draft = None
        self.issue_rows, self.rows, self.offsets = {}, {}, {}
        self.persisted_store = None
        self.loading_selection = False
        self.detached = False
        self._detach_blocked = False
        self.window = None
        self.issue_body = None
        self.issue_id = None
        self.limit = 100
        self.pinned = {}
        self.host_layouts = {}
        self.editor_expanded = False
        self.editor_ratio = .62
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *_: self.refresh_directory())
        self.feedback = tk.StringVar(value='新建问题后，勾选或拖入凭证。')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self, padding=(8, 4))
        top.grid(row=0, column=0, sticky='ew')
        self.heading = tk.StringVar(value='问题工作台')
        ttk.Label(top, textvariable=self.heading).pack(side='left')
        self.detach_button = ttk.Button(top, text='独立窗口', command=self.detach)
        self.detach_button.pack(side='right', padx=4)
        ttk.Button(top, text='收起', command=self.hide).pack(side='right')
        self.calc_body = ttk.Frame(self)
        self.calc_body.columnconfigure(0, weight=1)
        self.calc_body.rowconfigure(1, weight=1)
        self.calc_source = tk.StringVar()
        self.calc_source_label = ttk.Label(self.calc_body, textvariable=self.calc_source, padding=8, wraplength=420)
        self.calc_source_label.grid(row=0, column=0, sticky='ew')
        self.calc_hosts = {}
        self.grip = ttk.Frame(app.content, width=5, cursor='sb_h_double_arrow')
        self.grip.bind('<B1-Motion>', self._resize)
        self._build_issues(self)

    @property
    def issues_visible(self):
        return bool(self.issue_body and self.issue_body.winfo_ismapped())

    def _build_issues(self, host):
        from .table_tools import TableTools
        from .voucher_selection import VoucherSelection, CellTooltip, selection_bar
        self.issue_body = ttk.Frame(host)
        self.issue_body.columnconfigure(0, weight=1)
        self.issue_body.rowconfigure(1, weight=1)
        if host is self:
            self.issue_body.grid(row=1, column=0, sticky='nsew')
        else:
            header = ttk.Frame(self.issue_body, padding=8)
            header.grid(row=0, column=0, sticky='ew')
            ttk.Label(header, text='问题工作台').pack(side='left')
            ttk.Button(header, text='停靠右侧', command=self.dock).pack(side='right')
            ttk.Button(header, text='收起', command=self.hide_issues).pack(side='right', padx=4)
            self.issue_body.pack(fill='both', expand=True)
        self.outer = ttk.Panedwindow(self.issue_body, orient='horizontal' if self.detached else 'vertical')
        self.outer.grid(row=1, column=0, sticky='nsew')
        directory = ttk.Frame(self.outer, padding=6)
        header = ttk.Frame(directory)
        header.pack(fill='x')
        ttk.Label(header, text='尽调问题').pack(side='left')
        ttk.Button(header, text='新建问题', command=self.new_issue).pack(side='right')
        search = ttk.Entry(directory, textvariable=self.search)
        search.pack(fill='x', pady=4)
        self.tree = self._table(directory, [('title', '问题', 200), ('status', '状态', 80), ('count', '凭证', 50)], height=3)
        self.tree.column('title', stretch=True)
        CellTooltip(self.tree)
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.tree.bind('<Button-3>', self._issue_menu)
        self.tree.bind('<Button-2>', self._issue_menu)
        self.tree.tag_configure('drop', background='#DDEBDD')
        self.outer.add(directory, weight=1)
        right = ttk.Frame(self.outer)
        self.outer.add(right, weight=4)
        self.split = ttk.Panedwindow(right, orient='vertical')
        self.split.pack(fill='both', expand=True)
        self.split.bind('<Configure>', self._fit_editor_panes)
        self.split.bind('<ButtonRelease-1>', self._remember_editor_ratio)
        vouchers = ttk.Frame(self.split, padding=6)
        header = ttk.Frame(vouchers)
        header.pack(fill='x')
        self.issue_title = tk.StringVar(value='请选择问题')
        ttk.Label(header, textvariable=self.issue_title, wraplength=280).pack(side='left')
        more = ttk.Menubutton(header, text='更多')
        more.pack(side='right')
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label='加入其他问题…', command=lambda: self.choose_issue(self.voucher_selection.action_keys()))
        menu.add_command(label='移除所选关联', command=self.remove_selected_links)
        menu.add_separator()
        menu.add_command(label='查看所选归集快照', command=self.open_stored_snapshot)
        menu.add_command(label='确认已复核所选凭证', command=self.confirm_selected)
        menu.add_command(label='重读已保存问题…', command=lambda: self.reload_issue(self.issue_id) if self.issue_id else None)
        menu.add_command(label='删除当前问题…', command=lambda: self.remove_issue(self.issue_id) if self.issue_id else None)
        more.configure(menu=menu)
        self.voucher_view = self._table(vouchers, [('date', '日期', 80), ('voucher', '凭证号', 72), ('summary', '摘要', 116), ('amount', '金额', 116), ('status', '状态', 120)], height=7)
        self.voucher_view.column('summary', stretch=True)
        self.voucher_view.column('amount', anchor='e')
        self.voucher_tools = TableTools(self.voucher_view, set(), show_stats=False)
        self.voucher_selection = VoucherSelection(self.voucher_view, lambda row: self.rows[row][1])
        selection_bar(vouchers, self.voucher_selection)
        CellTooltip(self.voucher_view)
        self.voucher_view.bind('<Double-1>', self._double)
        self.voucher_tools.menu.add_separator()
        self.voucher_tools.menu.add_command(label='移除所选关联', command=self.remove_selected_links)
        self.more_button = ttk.Button(vouchers, text='加载更多凭证', command=lambda: self.load_issue(self.issue_id, more=True))
        self.more_button.pack(side='bottom', anchor='e', pady=2, before=self.voucher_view.master)
        self.split.add(vouchers, weight=5)
        self.editor_holder = ttk.Frame(self.split)
        editor_toggle = ttk.Button(right, text='问题描述与核查笔记 ▾', command=self.toggle_editor)
        editor_toggle.pack(side='bottom', fill='x', before=self.split)
        self.editor_toggle = editor_toggle
        self.canvas = tk.Canvas(self.editor_holder, highlightthickness=0, width=1, height=170, background='#FAF9F5')
        scroll = ttk.Scrollbar(self.editor_holder, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.detail = ttk.Frame(self.canvas, padding=8)
        self.detail_window = self.canvas.create_window(0, 0, window=self.detail, anchor='nw')
        self.detail.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.detail_window, width=e.width))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        if self.editor_expanded:
            self.split.add(self.editor_holder, weight=2)
        ttk.Label(self.issue_body, textvariable=self.feedback, style='Hint.TLabel', wraplength=440, padding=6).grid(row=2, column=0, sticky='ew')
        self.empty_detail()
        if hasattr(self.app, 'drag'):
            self.app.drag.attach(self.voucher_view, lambda row: self.rows[row][1])
        self.refresh_directory()
        if self.issue_id and self.app.store:
            self.edit_issue(self.issue_id)
        self._update_editor_label()
        self.issue_body.after_idle(self._initial_sashes)

    @staticmethod
    def _table(parent, columns, height=5):
        holder = ttk.Frame(parent)
        holder.pack(fill='both', expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        view = ttk.Treeview(holder, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=height)
        for key, title, width in columns:
            view.heading(key, text=title)
            view.column(key, width=width, minwidth=60, stretch=False)
        y = ttk.Scrollbar(holder, command=view.yview)
        x = ttk.Scrollbar(holder, orient='horizontal', command=view.xview)
        view.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        view.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        return view

    def _initial_sashes(self):
        if self.outer.winfo_exists():
            self.outer.sashpos(0, 245 if self.detached else 145)
        self._fit_editor_panes()

    def _fit_editor_panes(self, _event=None):
        if self.editor_expanded and len(self.split.panes()) > 1:
            height = self.split.winfo_height()
            if height > 125:
                self.split.sashpos(0, max(60, min(height - 65, int(height * self.editor_ratio))))

    def _remember_editor_ratio(self, _event=None):
        if self.editor_expanded and len(self.split.panes()) > 1:
            self.editor_ratio = self.split.sashpos(0) / max(1, self.split.winfo_height())

    def _update_editor_label(self):
        self.editor_toggle.configure(text='问题描述与核查笔记 ' + ('▾' if self.editor_expanded else '▸'))

    def toggle_editor(self):
        self.editor_expanded = not self.editor_expanded
        if self.editor_expanded:
            self.split.add(self.editor_holder, weight=2)
            self.issue_body.after_idle(self._fit_editor_panes)
        else:
            self.split.forget(self.editor_holder)
        self._update_editor_label()

    def _clear_detail(self):
        if self.draft:
            self.draft.dispose()
        for child in self.detail.winfo_children():
            child.destroy()
        self.draft = None
        self.canvas.yview_moveto(0)
        self.editor_owner = ttk.Frame(self.detail)
        self.editor_owner.pack()

    def empty_detail(self):
        self._clear_detail()
        ttk.Label(self.detail, text='选择问题后编辑描述与核查笔记。').pack(anchor='w')

    def calculator_host(self, name):
        holder = ttk.Frame(self.calc_body)
        canvas = tk.Canvas(holder, highlightthickness=0, width=1, height=1, background='#FAF9F5')
        bar = ttk.Scrollbar(holder, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        frame = ttk.Frame(canvas)
        frame.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        self.calc_hosts[name] = holder
        return frame

    def set_calculator(self, tools, name, reveal=False):
        if not reveal and self.visible and self.mode == 'calculator' and tools is self.active_tools:
            return self.hide()
        if not self.flush():
            return False
        self.last_tools = (tools, name)
        self.active_tools = tools
        self.mode = 'calculator'
        self.heading.set('计算器')
        self.detach_button.pack_forget()
        if not self.detached:
            self.issue_body.grid_remove()
        self.calc_body.grid(row=1, column=0, sticky='nsew')
        for context, frame in self.calc_hosts.items():
            if context == name:
                frame.grid(row=1, column=0, sticky='nsew')
                tools.panel.grid(row=0, column=0, sticky='nsew')
                tools.set_compact_width(None)
            else:
                frame.grid_remove()
        self.calc_source.set(f'计算来源：{name}')
        self.visible = True
        self.app._resize_workspace()
        return True

    def show_calculator(self, toggle=False):
        if toggle and self.visible and self.mode == 'calculator':
            return self.hide()
        if not self.app.store:
            return False
        page = self.app.current_page()
        if page == 'reports':
            kind = self.app.report_kind.get()
            tools, name = self.app.report_tools[kind], self.app.report_labels[kind]
        elif page in ('accounts', 'entries'):
            tools, name = (self.app.balance_tools, '科目总览') if page == 'accounts' else (self.app.detail_tools, '明细账')
        elif self.last_tools:
            tools, name = self.last_tools
        else:
            tools, name = self.app.detail_tools, '明细账'
        return self.set_calculator(tools, name, reveal=True)

    def page_changed(self):
        if self.visible and self.mode == 'calculator' and self.app.current_page() in ('accounts', 'entries', 'reports'):
            self.show_calculator()

    def toggle(self):
        if self.detached:
            return self.show()
        return self.hide() if self.visible and self.mode == 'issues' else self.show()

    def show(self):
        if not self.app.store:
            self.app.status.set('请先打开账套。')
            return False
        if not self.flush():
            return False
        if self.detached:
            self.window.title('问题工作台 · ' + self.app.book_name.get())
            self.window.deiconify()
            self.window.lift()
            self.app._resize_workspace()
            return True
        if self.persisted_store is not self.app.store:
            self.reset()
        self.mode = 'issues'
        self.heading.set('问题工作台')
        if not self.detach_button.winfo_manager():
            self.detach_button.pack(side='right', padx=4)
        self.calc_body.grid_remove()
        self.issue_body.grid(row=1, column=0, sticky='nsew')
        self.visible = True
        self.refresh_directory()
        self.app._resize_workspace()
        return True

    def hide(self):
        if not self.flush():
            return False
        self.visible = False
        if self.active_tools:
            self.active_tools.panel.grid_remove()
        self.app._resize_workspace()
        return True

    def hide_issues(self):
        if not self.flush():
            return False
        if self.detached:
            self.window.withdraw()
        elif self.mode == 'issues':
            self.visible = False
        self.app._resize_workspace()
        return True

    def _rehost(self, detached):
        if not self.flush():
            return False
        self.host_layouts[self.detached] = {'tree_y': self.tree.yview()[0], 'x': self.voucher_view.xview()[0], 'columns': {key: self.voucher_view.column(key, 'width') for key in self.voucher_view['columns']}}
        old_scroll = self.voucher_view.yview()[0]
        old_checks = set(self.voucher_selection.checked)
        old_sashes = [self.split.sashpos(i) for i in range(max(0, len(self.split.panes()) - 1))]
        if self.draft:
            self.draft.dispose()
        self.issue_body.destroy()
        self.draft = None
        self.detached = detached
        if detached:
            if self.window is None:
                self.window = tk.Toplevel(self.app)
                self.window.title('问题工作台 · ' + self.app.book_name.get())
                self.window.geometry('1120x740')
                self.window.minsize(760, 480)
                self.window.protocol('WM_DELETE_WINDOW', self.hide_issues)
            self._build_issues(self.window)
            self.window.deiconify()
            if self.mode == 'issues':
                self.visible = False
        else:
            self.window.withdraw()
            self._build_issues(self)
            self.mode = 'issues'
            self.heading.set('问题工作台')
            if not self.detach_button.winfo_manager():
                self.detach_button.pack(side='right', padx=4)
            self.visible = True
            self.calc_body.grid_remove()
        self._detach_blocked = False
        self.voucher_selection.checked = old_checks
        self.voucher_selection.refresh()
        def restore():
            if self.voucher_view.winfo_exists():
                self.voucher_view.yview_moveto(old_scroll)
                layout = self.host_layouts.get(detached)
                if layout:
                    self.tree.yview_moveto(layout['tree_y'])
                    for key, width in layout['columns'].items():
                        self.voucher_view.column(key, width=width)
                    self.voucher_view.xview_moveto(layout['x'])
                for i, position in enumerate(old_sashes):
                    if i + 1 < len(self.split.panes()):
                        self.split.sashpos(i, position)
        self.issue_body.after_idle(restore)
        self.app._resize_workspace()
        return True

    def detach(self):
        return self.show() if self.detached else self._rehost(True)

    def dock(self):
        if self.app.content.winfo_width() < self.width + 525:
            self.feedback.set('主窗口空间不足，请加宽主窗口后停靠。')
            return False
        return self._rehost(False)

    def layout(self):
        self.grid_remove()
        self.grip.grid_remove()
        if not self.visible:
            self._nav_state()
            return
        available = self.app.content.winfo_width()
        if self.mode == 'issues' and not self.detached and not self._detach_blocked and 1 < available < self.width + 525:
            if not getattr(self, '_detach_job', None):
                def detach():
                    self._detach_job = None
                    if self.visible and not self.detached and self.mode == 'issues':
                        if not self.detach():
                            self._detach_blocked = True
                            self.layout()
                self._detach_job = self.after_idle(detach)
            return
        if available >= self.width + 525:
            self._detach_blocked = False
        width = self.width if self.mode == 'issues' else min(360, max(260, available - 525))
        self.configure(width=width)
        self.grid_propagate(False)
        self.grip.grid(row=0, column=1, sticky='ns')
        self.grid(row=0, column=2, sticky='nsew')
        self._nav_state()

    def _nav_state(self):
        if hasattr(self.app, 'workbench_button'):
            issues_open = (self.detached and self.window.state() != 'withdrawn') or (not self.detached and self.visible and self.mode == 'issues')
            self.app.workbench_button.state(['selected' if issues_open else '!selected'])
            self.app.workbench_button.configure(text='问题工作台（独立显示）' if self.detached and self.issues_visible else '问题工作台')
        if hasattr(self.app, 'calculator_button'):
            self.app.calculator_button.state(['selected' if self.visible and self.mode == 'calculator' else '!selected'])

    def _resize(self, event):
        maximum = max(420, self.app.content.winfo_width() - 525)
        self.width = max(420, min(maximum, self.app.content.winfo_rootx() + self.app.content.winfo_width() - event.x_root))
        self.layout()

    def reset(self):
        if self.draft:
            self.draft.dispose()
        self.draft = None
        self.current = self.issue_id = None
        self.limit = 100
        self._detach_blocked = False
        self.pinned.clear()
        self.host_layouts.clear()
        self.search.set('')
        self.persisted_store = self.app.store
        self.rows.clear()
        self.issue_rows.clear()
        self.offsets.clear()
        self.visible = False
        if self.detached:
            self.window.withdraw()
            self.issue_body.destroy()
            self.detached = False
            self._build_issues(self)
        self.tree.delete(*self.tree.get_children())
        self.voucher_view.delete(*self.voucher_view.get_children())
        self.voucher_selection.clear()
        self.empty_detail()
        self.refresh_directory()

    def flush(self):
        if self.draft and not self.draft.flush():
            self.app.status.set(self.feedback.get())
            return False
        return self.app.note_hub.flush()

    def refresh_directory(self):
        if not self.app.store or not self.issue_body:
            return
        items = data.list_issues(self.app.store)
        self.issue_rows = {item['id']: item for item in items}
        needle = self.search.get().strip().lower()
        desired = {f'i:{item["id"]}': item for item in items if not needle or needle in (item['title'] + item['status']).lower()}
        self.loading_selection = True
        for node in self.tree.get_children():
            if node not in desired:
                self.tree.delete(node)
        for node, item in desired.items():
            values = (item['title'], item['status'], item['count'])
            if self.tree.exists(node):
                self.tree.item(node, values=values)
            else:
                self.tree.insert('', 'end', iid=node, values=values)
        if self.issue_id and self.tree.exists(f'i:{self.issue_id}'):
            self.tree.selection_set(f'i:{self.issue_id}')
        self.loading_selection = False

    def _select(self, _event=None):
        if self.loading_selection:
            return
        selected = self.tree.selection()
        if selected:
            issue_id = int(selected[0][2:])
            if issue_id != self.issue_id:
                if not self.edit_issue(issue_id) and self.issue_id and self.tree.exists(f'i:{self.issue_id}'):
                    self.tree.selection_set(f'i:{self.issue_id}')

    def load_issue(self, issue_id, more=False):
        from .voucher_summary import summaries_for_keys
        if not issue_id or not self.app.store:
            return
        self.limit = self.limit + 100 if more else self.limit
        batch = data.issue_vouchers(self.app.store, issue_id, 0, self.limit)
        loaded_keys = {item['voucher_key'] for item in batch}
        batch.extend({'voucher_key': key} for key in self.pinned.get(issue_id, ()) if key not in loaded_keys)
        summaries = summaries_for_keys(self.app.store, [item['voucher_key'] for item in batch])
        selected = set(self.voucher_selection.action_keys())
        scroll = self.voucher_view.yview()[0]
        self.voucher_view.delete(*self.voucher_view.get_children())
        self.rows.clear()
        for index, item in enumerate(batch):
            key = item['voucher_key']
            row = summaries[key]
            node = f'v:{issue_id}:{index}'
            self.rows[node] = (issue_id, key)
            status = '；'.join(part for part in (row['status'] if row['status'] != '正常' else '', row['amount_status']) if part)
            self.voucher_view.insert('', 'end', iid=node, values=(row['date'], row['voucher'], row['summary'], row['amount'], status))
            if key in selected:
                self.voucher_view.selection_add(node)
        self.voucher_view.yview_moveto(scroll)
        self.voucher_tools.reset_selection()
        self.voucher_selection.set_scope(issue_id)
        self.offsets[issue_id] = len(batch)
        count = self.issue_rows.get(issue_id, {}).get('count', len(batch))
        self.more_button.configure(text=f'已显示 {len(batch)} / {count} 张 · 加载更多')
        if len(batch) < count:
            self.more_button.pack(side='bottom', anchor='e', pady=2, before=self.voucher_view.master)
        else:
            self.more_button.pack_forget()

    def new_issue(self):
        if not self.app.store or not self.flush():
            return
        title = simpledialog.askstring('新建尽调问题', '问题名称', parent=self.window if self.detached else self.app)
        if title is not None:
            try:
                issue_id = data.create_issue(self.app.store, title)
                self.show()
                self.edit_issue(issue_id)
                if not self.editor_expanded:
                    self.toggle_editor()
            except Exception as exc:
                messagebox.showerror('无法创建问题', str(exc), parent=self.app)

    def edit_issue(self, issue_id):
        if not self.flush():
            return False
        item = data.get_issue(self.app.store, issue_id)
        if self.issue_id != issue_id:
            self.limit = 100
        self.issue_id = issue_id
        self._clear_detail()
        self.current = ("issue", issue_id)
        node = f"i:{issue_id}"
        if self.tree.exists(node):
            self.selected_node = node
            self.loading_selection = True
            self.tree.selection_set(node)
            self.loading_selection = False
            self.tree.see(node)
        self.title_var = tk.StringVar(value=item["title"])
        self.status_var = tk.StringVar(value=item["status"])
        ttk.Label(self.detail, text="问题名称").pack(anchor="w")
        title = ttk.Entry(self.detail, textvariable=self.title_var)
        title.pack(fill="x", pady=(3, 8))
        ttk.Label(self.detail, text="处理状态").pack(anchor="w")
        status = ttk.Combobox(self.detail, textvariable=self.status_var, values=data.STATUSES, state="readonly")
        status.pack(fill="x", pady=(3, 8))
        ttk.Label(self.detail, text="问题描述").pack(anchor="w")
        self.description = tk.Text(self.detail, height=3, width=1, wrap="word", undo=True)
        self.description.pack(fill="x", pady=(3, 8))
        self.description.insert("1.0", item["description"])
        ttk.Label(self.detail, text="核查笔记").pack(anchor="w")
        self.notes = tk.Text(self.detail, height=6, width=1, wrap="word", undo=True)
        self.notes.pack(fill="x", pady=(3, 8))
        self.notes.insert("1.0", item["notes"])
        def capture():
            return (self.title_var.get(), self.status_var.get(), self.description.get("1.0", "end-1c"), self.notes.get("1.0", "end-1c"))
        def save(value):
            item["revision"] = data.save_issue(self.app.store, issue_id, item["revision"], *value)
            self.issue_title.set(value[0])
            self.refresh_directory()
        self.draft = Draft(self.editor_owner, capture, save, self.feedback)
        self.title_var.trace_add("write", self.draft.changed)
        self.status_var.trace_add("write", self.draft.changed)
        for widget in (title, status, self.description, self.notes):
            self.draft.watch(widget)
        self.feedback.set("已保存")
        self.issue_title.set(item["title"])
        self.refresh_directory()
        self.load_issue(issue_id)
        return True

    def reload_issue(self, issue_id):
        if messagebox.askyesno("读取已保存问题", "将当前问题描述和笔记复制到剪贴板，再读取最新保存内容？", parent=self.app):
            value = self.draft.capture()
            self.clipboard_clear()
            self.clipboard_append("\n\n".join(value))
            self.draft.baseline = value
            self.edit_issue(issue_id)

    def remove_issue(self, issue_id):
        if not self.flush():
            return
        item = next(row for row in data.list_issues(self.app.store) if row["id"] == issue_id)
        if messagebox.askyesno("删除问题", f'删除“{item["title"]}”及其 {item["count"]} 张凭证的关联？\n凭证关注及原始数据保留。', parent=self.app):
            data.delete_issue(self.app.store, issue_id)
            self.pinned.pop(issue_id, None)
            self.current = self.issue_id = None
            self.voucher_view.delete(*self.voucher_view.get_children())
            self.rows.clear()
            self.issue_title.set("请选择问题")
            self.empty_detail()
            self.refresh_directory()

    def choose_issue(self, keys):
        keys = (keys,) if isinstance(keys, str) else tuple(dict.fromkeys(keys))
        if not keys or not self.app.store or not self.flush():
            return
        items = data.list_issues(self.app.store)
        parent = self.window if self.detached and self.issues_visible else self.app
        dialog = tk.Toplevel(parent)
        dialog.title('加入尽调问题')
        dialog.transient(parent)
        dialog.geometry('420x320')
        ttk.Label(dialog, text=f'将 {len(keys)} 张凭证加入问题；已有的关联会跳过。', padding=10).pack(fill='x')
        choices = tk.Listbox(dialog, exportselection=False)
        choices.pack(fill='both', expand=True, padx=10)
        for item in items:
            choices.insert('end', f'{item["title"]}（{item["count"]} 张）')
        def add():
            if choices.curselection() and self.add_to_issue(items[choices.curselection()[0]]['id'], keys):
                dialog.destroy()
        def create():
            title = simpledialog.askstring('新建问题并加入', '问题名称', parent=dialog)
            if title is not None:
                try:
                    issue_id = data.create_issue_with_vouchers(self.app.store, title, keys)
                    self._after_add(issue_id, keys, {'added': len(keys), 'duplicates': 0})
                    dialog.destroy()
                except Exception as exc:
                    messagebox.showerror('无法加入', str(exc), parent=dialog)
        ttk.Button(dialog, text='加入所选问题', command=add).pack(pady=6)
        ttk.Button(dialog, text='新建问题并加入…', command=create).pack(pady=(0, 8))
        choices.bind('<Double-1>', lambda _e: add())
        if self.issue_id:
            for index, item in enumerate(items):
                if item['id'] == self.issue_id:
                    choices.selection_set(index)
        dialog.grab_set()

    def add_to_issue(self, issue_id, keys):
        keys = (keys,) if isinstance(keys, str) else tuple(dict.fromkeys(keys))
        if not keys or not self.flush():
            return False
        try:
            result = data.link_vouchers(self.app.store, issue_id, keys)
            self._after_add(issue_id, keys, result)
            return True
        except Exception as exc:
            self.feedback.set(f'加入失败：{exc}')
            return False

    def _after_add(self, issue_id, keys, result):
        self.show()
        self.refresh_directory()
        self.pinned[issue_id] = tuple(keys)
        self.edit_issue(issue_id)
        for row, pair in self.rows.items():
            if pair[1] in keys:
                self.voucher_view.selection_set(row)
                self.voucher_view.see(row)
                break
        for key in keys:
            self.app.note_hub.broadcast(key)
            self.app.update_mark_indicators(key)
        self.feedback.set(f'新增 {result["added"]} 张，已存在 {result["duplicates"]} 张')
        self.app.status.set(self.feedback.get())

    def remove_selected_links(self):
        keys = self.voucher_selection.action_keys()
        if not keys or not self.issue_id or not self.flush():
            return
        if not messagebox.askyesno('移除关联', f'从当前问题移除 {len(keys)} 张凭证？关注及备注保留。', parent=self.window if self.detached else self.app):
            return
        data.unlink_vouchers(self.app.store, self.issue_id, keys)
        self.pinned[self.issue_id] = tuple(key for key in self.pinned.get(self.issue_id, ()) if key not in keys)
        self.voucher_selection.clear()
        self.refresh_directory()
        self.load_issue(self.issue_id)
        self.feedback.set(f'已移除 {len(keys)} 张关联，关注及备注保留。')

    def remove_link(self, issue_id, key):
        if self.flush():
            data.unlink_vouchers(self.app.store, issue_id, [key])
            self.pinned[issue_id] = tuple(item for item in self.pinned.get(issue_id, ()) if item != key)
            self.refresh_directory()
            self.load_issue(issue_id)

    def confirm_selected(self):
        keys = self.voucher_selection.action_keys()
        if keys and self.flush() and messagebox.askyesno('确认复核', f'已核对 {len(keys)} 张凭证？更新归集快照，关注记录的复核状态另行确认。', parent=self.app):
            try:
                data.confirm_references(self.app.store, keys)
                self.load_issue(self.issue_id)
            except Exception as exc:
                self.feedback.set(str(exc))

    def open_key(self, key):
        if not self.flush():
            return
        try:
            item = data.voucher_preview(self.app.store, key)
        except ValueError as exc:
            messagebox.showinfo('无法打开凭证', str(exc), parent=self.app)
            return
        if item['entry_id'] is not None:
            return self.app.open_voucher(None, entry_id=item['entry_id'])
        self.open_snapshot(item)

    def open_stored_snapshot(self):
        keys = self.voucher_selection.action_keys()
        if len(keys) != 1 or not self.flush():
            self.feedback.set('请选择一张凭证查看归集快照。')
            return
        item = data.voucher_preview(self.app.store, keys[0])
        if item['snapshot']:
            self.open_snapshot({**item, 'lines': item['snapshot'], 'status': '归集时快照（只读）'})

    def open_snapshot(self, item):
        from .table_tools import TableTools
        window = tk.Toplevel(self.app)
        window.title('归集时快照 · ' + item['label'])
        window.geometry('940x520')
        ttk.Label(window, text=item['status'], padding=10).pack(fill='x')
        view = self._table(window, [('date', '日期', 100), ('name', '科目', 150), ('summary', '摘要', 250), ('debit', '借方', 120), ('credit', '贷方', 120), ('source', '来源', 300)])
        window.cell_tools = TableTools(view, {'debit', 'credit'})
        for row in item['lines']:
            view.insert('', 'end', values=(row['entry_date'], row['name'], row['summary'], format_money(row['debit_cents'], comma=True), format_money(row['credit_cents'], comma=True), f'{row["original_path"]} / {row["sheet"]}:{row["row_no"]}'))
        return window

    def _double(self, event):
        if self.voucher_view.identify_column(event.x) == '#0':
            return 'break'
        node = self.voucher_view.identify_row(event.y)
        if node in self.rows:
            self.open_key(self.rows[node][1])
        return 'break'

    def _issue_menu(self, event):
        node = self.tree.identify_row(event.y)
        if not node:
            return
        issue_id = int(node[2:])
        menu = tk.Menu(self.tree, tearoff=False)
        menu.add_command(label='编辑问题', command=lambda: self.edit_issue(issue_id))
        menu.add_command(label='删除问题…', command=lambda: self.remove_issue(issue_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class VoucherDrag:
    """Handle-only, multi-voucher dragging across the owning application's windows."""

    def __init__(self, app):
        self.app = app
        self.tag = f'VoucherDrag:{id(self)}'
        self.sources = {}
        self.pending = None
        self.active = False
        self.tip = None
        self.hover_job = self.hover_target = self.highlight = None
        self.restore_hidden = False
        for event, callback in (('<ButtonPress-1>', self.press), ('<B1-Motion>', self.motion), ('<ButtonRelease-1>', self.release), ('<Escape>', self.cancel)):
            app.bind_class(self.tag, event, callback)
        # Escape still cancels if focus moved into a different toplevel.
        app.bind_all('<Escape>', self.cancel, add='+')

    def attach(self, view, key_for_row, tree_handle=True):
        self.sources[view] = key_for_row
        tags = list(view.bindtags())
        if self.tag not in tags:
            # Checkbox interception must precede dragging.
            position = 1 if tags and str(tags[0]).startswith('VoucherSelection:') else 0
            tags.insert(position, self.tag)
            view.bindtags(tuple(tags))
        if tree_handle and not hasattr(view, 'voucher_selection'):
            view.configure(show='tree headings')
            view.column('#0', width=30, minwidth=30, stretch=False)
            view.heading('#0', text='⋮⋮')
        def clean(event):
            if event.widget is view:
                if self.pending and self.pending[0] is view:
                    self.cancel()
                self.sources.pop(view, None)
        view.bind('<Destroy>', clean, add='+')
        if hasattr(view, 'cell_tools'):
            menu = view.cell_tools.menu
            menu.add_separator()
            index = menu.index('end') + 1
            menu.add_command(label='加入问题…', command=lambda: self.choose_from_view(view))
            if view is getattr(self.app, 'detail_view', None):
                menu.add_command(label='关注所选', command=lambda: self.app.mark_selected_entry())
            def labels():
                selection = getattr(view, 'voucher_selection', None)
                count = len(selection.action_keys()) if selection else len(view.selection())
                menu.entryconfigure(index, label=f'加入问题…（{count} 张）', state='normal' if count else 'disabled')
            menu.configure(postcommand=labels)

    def keys_from_view(self, view):
        selection = getattr(view, 'voucher_selection', None)
        if selection:
            return selection.action_keys()
        return tuple(dict.fromkeys(self.sources[view](row) for row in view.selection()))

    def choose_from_view(self, view):
        self.app.workbench.choose_issue(self.keys_from_view(view))

    def press(self, event):
        view = event.widget
        if not self.app.store or view not in self.sources:
            return
        row = view.identify_row(event.y)
        if not row or view.identify_column(event.x) != '#0' or view.identify_region(event.x, event.y) == 'separator':
            return
        selection = getattr(view, 'voucher_selection', None)
        if selection and event.x <= 28:
            return
        try:
            keys = selection.drag_keys(row) if selection else (self.sources[view](row),)
        except (ValueError, KeyError, TypeError):
            return 'break'
        if keys:
            self.restore_hidden = False
            self.original_calculator = None
            self.pending = (view, keys, event.x_root, event.y_root)
            view.focus_set()
            if not selection or keys == (self.sources[view](row),):
                view.selection_set(row)
        return 'break'

    def motion(self, event):
        if not self.pending:
            return
        view, keys, start_x, start_y = self.pending
        if not self.active and abs(event.x_root - start_x) + abs(event.y_root - start_y) < 6:
            return 'break'
        panel = self.app.workbench
        if not self.active:
            self.active = True
            self.restore_hidden = not panel.issues_visible
            self.original_calculator = panel.last_tools if panel.visible and panel.mode == 'calculator' else None
            self.tip = tk.Toplevel(self.app)
            self.tip.overrideredirect(True)
            self.tip.attributes('-topmost', True)
            self.tip_label = ttk.Label(self.tip, padding=6, relief='solid')
            self.tip_label.pack()
            # A local Tk grab receives native motion/release across same-process windows.
            view.grab_set()
        target = self.target_at(event.x_root, event.y_root)
        label = '松开取消'
        if isinstance(target, int):
            label = '加入：' + panel.issue_rows[target]['title']
        elif target == 'choose':
            label = '松开选择问题'
        self.tip_label.configure(text=f'{len(keys)} 张凭证 · {label}')
        self.tip.geometry(f'{event.x_root + 16:+d}{event.y_root + 18:+d}')
        self._hover(target, event.x_root, event.y_root)
        return 'break'

    @staticmethod
    def contains(widget, x, y):
        return widget.winfo_ismapped() and widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width() and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height()

    def target_at(self, x, y):
        panel = self.app.workbench
        if not panel.issues_visible:
            return None
        if self.contains(panel.tree, x, y):
            node = panel.tree.identify_row(y - panel.tree.winfo_rooty())
            return int(node[2:]) if node.startswith('i:') else 'choose'
        if self.contains(panel.voucher_view, x, y):
            return panel.issue_id or 'choose'
        if self.contains(panel.issue_body, x, y):
            return 'choose'
        return None

    def _hover(self, target, x, y):
        panel = self.app.workbench
        edge = self.app.winfo_rootx() + self.app.winfo_width() - 24 <= x <= self.app.winfo_rootx() + self.app.winfo_width() and self.app.winfo_rooty() <= y <= self.app.winfo_rooty() + self.app.winfo_height()
        hover = 'edge' if edge and not panel.issues_visible else target
        if hover != self.hover_target:
            if self.hover_job:
                self.app.after_cancel(self.hover_job)
                self.hover_job = None
            if self.highlight and panel.tree.exists(self.highlight):
                panel.tree.item(self.highlight, tags=())
            self.highlight = f'i:{target}' if isinstance(target, int) else None
            if self.highlight and panel.tree.exists(self.highlight):
                panel.tree.item(self.highlight, tags=('drop',))
            self.hover_target = hover
            if hover == 'edge':
                self.hover_job = self.app.after(450, self._open_edge)
        for view in (panel.tree, panel.voucher_view):
            if self.contains(view, x, y):
                local = y - view.winfo_rooty()
                if local < 30:
                    view.yview_scroll(-1, 'units')
                elif local > view.winfo_height() - 20:
                    view.yview_scroll(1, 'units')

    def _open_edge(self):
        self.hover_job = None
        if self.active:
            self.app.workbench.show()

    def release(self, event):
        if not self.pending:
            return
        keys = self.pending[1]
        target = self.target_at(event.x_root, event.y_root) if self.active else None
        was_active = self.active
        self._cleanup()
        success = False
        if was_active and isinstance(target, int):
            success = self.app.workbench.add_to_issue(target, keys)
        elif was_active and target == 'choose':
            self.app.workbench.choose_issue(keys)
            success = True
        if not success:
            self._restore_panel()
        return 'break'

    def cancel(self, _event=None):
        if self.pending:
            self._cleanup()
            self._restore_panel()
            return 'break'

    def _restore_panel(self):
        if self.restore_hidden:
            self.app.workbench.hide_issues()
        if self.original_calculator:
            tools, name = self.original_calculator
            self.app.workbench.set_calculator(tools, name, reveal=True)

    def _cleanup(self):
        if self.hover_job:
            self.app.after_cancel(self.hover_job)
            self.hover_job = None
        panel = self.app.workbench
        if self.highlight and panel.tree.exists(self.highlight):
            panel.tree.item(self.highlight, tags=())
        self.highlight = self.hover_target = None
        if self.pending:
            try:
                self.pending[0].grab_release()
            except tk.TclError:
                pass
        self.pending = None
        self.active = False
        if self.tip:
            self.tip.destroy()
            self.tip = None
