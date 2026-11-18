"""Issue workbench, local autosave, and explicit voucher actions."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import issue_store as data
from . import theme
from .importer import format_money
from .voucher_notes import voucher_key
from .workbench_support import TOOL_GRIP_WIDTH, link_feedback, ordered_issue_choices, tool_width_limits


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
        self.width = None
        self.calculator_width = 300
        self.active_tools = self.last_tools = None
        self.current = self.draft = None
        self.issue_rows, self.rows, self.offsets = {}, {}, {}
        self.persisted_store = None
        self.loading_selection = False
        self.detached = False
        self._resize_start = None
        self.window = None
        self.recent_issue_ids = []
        self._drag_highlight = None
        self.issue_body = None
        self.issue_id = None
        self.limit = 100
        self.pinned = {}
        self.host_layouts = {}
        self.view_mode = 'directory'
        self.selected_issue_id = None
        self.directory_y = 0
        self.directory_query = ''
        self.detail_tab = 'vouchers'
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *_: self.refresh_directory())
        self.feedback = tk.StringVar(value='勾选凭证或框选单元格，再点击“加入问题…”。')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self, padding=(8, 4))
        top.grid(row=0, column=0, sticky='ew')
        self.heading = tk.StringVar(value='问题工作台')
        ttk.Label(top, textvariable=self.heading, style="Title.TLabel").pack(side='left')
        ttk.Button(top, text='×', width=2, style="Ghost.TButton", command=self.hide).pack(side='right')
        self.detach_button = ttk.Button(top, text='独立窗口', style='Ghost.TButton', command=self.detach)
        self.detach_button.pack(side='right', padx=4)
        self.calc_body = ttk.Frame(self)
        self.calc_body.columnconfigure(0, weight=1)
        self.calc_body.rowconfigure(1, weight=1)
        self.calc_source = tk.StringVar()
        self.calc_source_label = ttk.Label(self.calc_body, textvariable=self.calc_source, padding=8, wraplength=420)
        self.calc_source_label.grid(row=0, column=0, sticky='ew')
        self.calc_hosts = {}
        self.grip = ttk.Frame(app.content, width=TOOL_GRIP_WIDTH, cursor='sb_h_double_arrow', style='Resize.TFrame')
        self.grip.bind('<ButtonPress-1>', self._start_resize)
        self.grip.bind('<B1-Motion>', self._resize)
        self.grip.bind('<ButtonRelease-1>', self._finish_resize)
        app.navigation_tooltip.attach(self.grip, lambda: '左右拖动，调整主区域和工具区的宽度')
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

        self.pages = ttk.Frame(self.issue_body)
        self.pages.grid(row=1, column=0, sticky='nsew')
        self.pages.columnconfigure(0, weight=1)
        self.pages.rowconfigure(0, weight=1)
        self.directory_body = ttk.Frame(self.pages, padding=6)
        self.directory_body.grid(row=0, column=0, sticky='nsew')
        header = ttk.Frame(self.directory_body)
        header.pack(fill='x')
        ttk.Label(header, text='尽调问题', style="Title.TLabel").pack(side='left')
        search = ttk.Entry(self.directory_body, textvariable=self.search)
        search.pack(fill='x', pady=4)
        self.new_button = ttk.Button(self.directory_body, text='新建问题', style="Accent.TButton", command=self.new_issue)
        self.new_button.pack(side='bottom', fill='x', pady=(4, 0))
        self.tree = self._table(self.directory_body, [('title', '问题名称', 300), ('count', '凭证数', 72)], height=14, horizontal=False)
        self.tree.column('title', stretch=True)
        self.tree.column('count', anchor='e', minwidth=65)
        CellTooltip(self.tree)
        self.tree.bind('<<TreeviewSelect>>', self._select)
        self.tree.bind('<Double-1>', self._open_selected_issue)
        self.tree.bind('<Return>', self._open_selected_issue)
        self.tree.bind('<Button-3>', self._issue_menu)
        self.tree.bind('<Button-2>', self._issue_menu)

        self.details_body = ttk.Frame(self.pages, padding=6)
        self.details_body.grid(row=0, column=0, sticky='nsew')
        controls = ttk.Frame(self.details_body)
        controls.pack(fill='x')
        self.back_button = ttk.Button(controls, text='‹ 返回问题列表', style="Ghost.TButton", command=self.back_to_directory)
        self.back_button.pack(side='left')
        more = ttk.Menubutton(controls, text='更多')
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
        self.issue_switch_var = tk.StringVar()
        self.issue_switch = ttk.Combobox(controls, textvariable=self.issue_switch_var,
                                         state='readonly', width=16)
        self.issue_switch.pack(side='left', fill='x', expand=True, padx=6)
        self.issue_switch.bind('<<ComboboxSelected>>', self._switch_issue)
        self.issue_switch_values = ()
        self.issue_title = tk.StringVar(value='请选择问题')
        self.caption = ttk.Label(self.details_body, textvariable=self.issue_title, wraplength=380, padding=(0, 5))
        self.caption.pack(fill='x')
        self.details_body.bind('<Configure>', lambda e: self.caption.configure(wraplength=max(150, e.width - 12)))
        self.detail_tabs = ttk.Notebook(self.details_body)
        self.detail_tabs.pack(fill='both', expand=True)
        self.vouchers_page = ttk.Frame(self.detail_tabs)
        self.record_page = ttk.Frame(self.detail_tabs)
        self.detail_tabs.add(self.vouchers_page, text='相关凭证')
        self.detail_tabs.add(self.record_page, text='问题记录')
        self.detail_tabs.bind('<<NotebookTabChanged>>', self._detail_tab_changed)
        vouchers = self.vouchers_page
        self.voucher_view = self._table(vouchers, [('date', '日期', 80), ('voucher', '凭证号', 72), ('summary', '摘要', 116), ('amount', '金额', 116), ('status', '状态', 120)], height=12)
        self.voucher_view.column('summary', stretch=True)
        self.voucher_view.column('amount', anchor='e')
        self.voucher_tools = TableTools(self.voucher_view, set(), show_stats=False)
        self.voucher_selection = VoucherSelection(self.voucher_view, lambda row: self.rows[row][1])
        selection_bar(vouchers, self.voucher_selection)
        self.locate_button = ttk.Button(vouchers, text='定位到当前明细', command=self.locate_selected)
        self.locate_button.pack(side='bottom', anchor='e', pady=2, before=self.voucher_view.master)
        CellTooltip(self.voucher_view)
        self.voucher_view.bind('<Double-1>', self._double)
        self.voucher_tools.menu.add_separator()
        self.voucher_tools.menu.add_command(label='移除所选关联', command=self.remove_selected_links)
        self.more_button = ttk.Button(vouchers, text='加载更多凭证', command=lambda: self.load_issue(self.issue_id, more=True))

        self.canvas = tk.Canvas(self.record_page, highlightthickness=0, width=1, height=1, background=theme.CANVAS)
        scroll = ttk.Scrollbar(self.record_page, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.detail = ttk.Frame(self.canvas, padding=8)
        self.detail_window = self.canvas.create_window(0, 0, window=self.detail, anchor='nw')
        self.detail.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.detail_window, width=e.width))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        feedback = ttk.Label(self.issue_body, textvariable=self.feedback, style='Hint.TLabel', wraplength=380, padding=6)
        feedback.grid(row=2, column=0, sticky='ew')
        feedback.bind('<Configure>', lambda event: feedback.configure(wraplength=max(100, event.width - 12)))
        self.empty_detail()
        if hasattr(self.app, 'voucher_actions'):
            self.app.voucher_actions.attach(self.voucher_view, lambda row: self.rows[row][1])
        self.refresh_directory()
        if self.issue_id and self.app.store and self.view_mode == 'detail':
            self.edit_issue(self.issue_id, tab=self.detail_tab, reveal=False)
        self._display_issue_view()

    @staticmethod
    def _table(parent, columns, height=5, horizontal=True):
        holder = ttk.Frame(parent)
        holder.pack(fill='both', expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        view = ttk.Treeview(holder, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=height)
        for key, title, width in columns:
            view.heading(key, text=title)
            view.column(key, width=width, minwidth=60, stretch=False)
        y = ttk.Scrollbar(holder, command=view.yview)
        view.configure(yscrollcommand=y.set)
        view.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        if horizontal:
            x = ttk.Scrollbar(holder, orient='horizontal', command=view.xview)
            view.configure(xscrollcommand=x.set)
            x.grid(row=1, column=0, sticky='ew')
        return view

    def _display_issue_view(self):
        if self.view_mode == 'detail':
            self.directory_body.grid_remove()
            self.details_body.grid(row=0, column=0, sticky='nsew')
        else:
            self.details_body.grid_remove()
            self.directory_body.grid(row=0, column=0, sticky='nsew')
            self.tree.yview_moveto(self.directory_y)

    def back_to_directory(self):
        if not self.flush():
            return False
        if self.view_mode == 'directory':
            self.directory_y = self.tree.yview()[0]
        self.view_mode = 'directory'
        self._display_issue_view()
        return True

    def _open_selected_issue(self, event=None):
        if event is not None and event.type == tk.EventType.ButtonPress:
            row = self.tree.identify_row(event.y)
            if not row:
                return 'break'
            self.selected_issue_id = int(row[2:])
        if self.selected_issue_id is not None:
            self.edit_issue(self.selected_issue_id)
        return 'break'

    def _switch_issue(self, _event=None):
        index = self.issue_switch.current()
        if not 0 <= index < len(self.issue_switch_values):
            return
        issue_id = self.issue_switch_values[index]
        if issue_id != self.issue_id and not self.edit_issue(issue_id, tab=self.detail_tab):
            self._refresh_issue_switch()

    def _refresh_issue_switch(self):
        items = [self.issue_rows[issue_id] for issue_id in sorted(self.issue_rows)]
        self.issue_switch_values = tuple(item['id'] for item in items)
        self.issue_switch.configure(values=[item['title'] for item in items])
        current = self.issue_rows.get(self.issue_id)
        self.issue_switch_var.set(current['title'] if current else '')

    def _detail_tab_changed(self, _event=None):
        selected = self.detail_tabs.select()
        tab = 'record' if selected == str(self.record_page) else 'vouchers'
        if tab == self.detail_tab:
            return
        if not self.flush():
            previous = self.record_page if self.detail_tab == 'record' else self.vouchers_page
            self.detail_tabs.select(previous)
            return
        self.detail_tab = tab

    def _update_issue_caption(self):
        if self.issue_id not in self.issue_rows:
            return
        item = self.issue_rows[self.issue_id]
        self.issue_title.set(f'{item["title"]} · {item["count"]} 张凭证')
        self.detail_tabs.tab(self.vouchers_page, text=f'相关凭证（{item["count"]}）')

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
        canvas = tk.Canvas(holder, highlightthickness=0, width=1, height=1, background=theme.CANVAS)
        bar = ttk.Scrollbar(holder, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        frame = ttk.Frame(canvas)
        frame.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        def resize(event):
            canvas.itemconfigure(window, width=event.width, height=max(event.height, frame.winfo_reqheight()))
        canvas.bind('<Configure>', resize)
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
                tools.set_compact_width(self.calculator_width - 8)
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

    def show(self, reset_directory=True):
        if not self.app.store:
            self.app.status.set('请先打开账套。')
            return False
        if not self.flush():
            return False
        if reset_directory and not self.back_to_directory():
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
        directory_y = self.tree.yview()[0] if self.view_mode == 'directory' else self.directory_y
        selected_issue = self.selected_issue_id
        record_y = self.canvas.yview()[0]
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
        self.selected_issue_id = selected_issue
        self.directory_y = directory_y
        self.refresh_directory()
        self._display_issue_view()
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
                self.tree.yview_moveto(directory_y)
                self.canvas.yview_moveto(record_y)
        self.issue_body.after_idle(restore)
        self.app._resize_workspace()
        return True

    def detach(self):
        return self.show(reset_directory=False) if self.detached else self._rehost(True)

    def dock(self):
        return self._rehost(False)

    def layout(self):
        if hasattr(self.app, 'table_fit'):
            self.app.table_fit.schedule()
        if not self.visible:
            self._resize_start = None
            self.grid_remove()
            self.grip.grid_remove()
            self._nav_state()
            return
        available = self.app.content.winfo_width()
        lower, upper = tool_width_limits(available, self.mode)
        desired = self.calculator_width
        if self.mode == 'issues':
            desired = self.width or min(420, (available - TOOL_GRIP_WIDTH) * 7 // 16)
        width = max(lower, min(upper, desired))
        self.configure(width=width)
        self.grid_propagate(False)
        self.grip.grid(row=0, column=1, sticky='ns')
        self.grid(row=0, column=2, sticky='nsew')
        if self.mode == 'calculator' and self.active_tools:
            self.active_tools.set_compact_width(width - 8)
            self.calc_source_label.configure(wraplength=width - 16)
        self._nav_state()

    def _start_resize(self, event):
        self._resize_start = (event.x_root, self.winfo_width())

    def _finish_resize(self, _event):
        self._resize_start = None

    def _nav_state(self):
        if hasattr(self.app, 'workbench_button'):
            if hasattr(self.app, 'update_workbench_presentation'):
                self.app.update_workbench_presentation()
            else:
                issues_open = (self.detached and self.window.state() != 'withdrawn') or (not self.detached and self.visible and self.mode == 'issues')
                self.app.workbench_button.state(['selected' if issues_open else '!selected'])
        if hasattr(self.app, 'calculator_button'):
            self.app.calculator_button.state(['selected' if self.visible and self.mode == 'calculator' else '!selected'])

    def _resize(self, event):
        if self._resize_start is None:
            return
        available = self.app.content.winfo_width()
        minimum, maximum = tool_width_limits(available, self.mode)
        start_x, start_width = self._resize_start
        desired = start_width + start_x - event.x_root
        width = max(minimum, min(maximum, desired))
        if self.mode == 'issues':
            self.width = width
        else:
            self.calculator_width = width
        self.layout()

    def reset(self):
        if self.draft:
            self.draft.dispose()
        self.draft = None
        self.current = self.issue_id = None
        self.selected_issue_id = None
        self.view_mode = 'directory'
        self.detail_tab = 'vouchers'
        self.directory_y = 0
        self.directory_query = ''
        self.limit = 100
        self.recent_issue_ids.clear()
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
        self._display_issue_view()

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
        self.app.refresh_workbench_badge(len(items))
        needle = self.search.get().strip().lower()
        changed_query = needle != self.directory_query
        self.directory_query = needle
        position = 0 if changed_query else (self.tree.yview()[0] if self.view_mode == 'directory' else self.directory_y)
        desired = {f'i:{item["id"]}': item for item in items if not needle or needle in (item['title'] + item['status']).lower()}
        self.loading_selection = True
        try:
            for node in self.tree.get_children():
                if node not in desired:
                    self.tree.delete(node)
            for index, (node, item) in enumerate(desired.items()):
                values = (item['title'], item['count'])
                if self.tree.exists(node):
                    self.tree.item(node, values=values)
                else:
                    self.tree.insert('', 'end', iid=node, values=values)
                self.tree.move(node, '', index)
            node = f'i:{self.selected_issue_id}'
            if self.selected_issue_id is not None and self.tree.exists(node):
                self.tree.selection_set(node)
                self.tree.focus(node)
            self.tree.yview_moveto(position)
            self.directory_y = position
        finally:
            self.loading_selection = False
        self._update_issue_caption()
        self._refresh_issue_switch()

    def _select(self, _event=None):
        if self.loading_selection:
            return
        selected = self.tree.selection()
        if selected:
            self.selected_issue_id = int(selected[0][2:])

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
                self.selected_issue_id = issue_id
                self.search.set('')
                self.show()
                self.refresh_directory()
                self.tree.see(f'i:{issue_id}')
            except Exception as exc:
                messagebox.showerror('无法创建问题', str(exc), parent=self.app)

    def edit_issue(self, issue_id, tab='vouchers', reveal=True):
        if not self.flush():
            return False
        item = data.get_issue(self.app.store, issue_id)
        if reveal and self.view_mode == 'directory':
            self.directory_y = self.tree.yview()[0]
        if reveal:
            self.selected_issue_id = issue_id
        if self.issue_id != issue_id:
            self.limit = 100
        self.issue_id = issue_id
        self._clear_detail()
        self.current = ("issue", issue_id)
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
        self.refresh_directory()
        self.load_issue(issue_id)
        self.detail_tab = tab
        self.detail_tabs.select(self.record_page if tab == 'record' else self.vouchers_page)
        if reveal:
            self.view_mode = 'detail'
            self._display_issue_view()
        return True

    def reload_issue(self, issue_id):
        if messagebox.askyesno("读取已保存问题", "将当前问题描述和笔记复制到剪贴板，再读取最新保存内容？", parent=self.app):
            value = self.draft.capture()
            self.clipboard_clear()
            self.clipboard_append("\n\n".join(value))
            self.draft.baseline = value
            self.edit_issue(issue_id, tab='record')

    def remove_issue(self, issue_id):
        if not self.flush():
            return
        item = next(row for row in data.list_issues(self.app.store) if row["id"] == issue_id)
        if messagebox.askyesno("删除问题", f'删除“{item["title"]}”及其 {item["count"]} 张凭证的关联？\n凭证关注及原始数据保留。', parent=self.app):
            data.delete_issue(self.app.store, issue_id)
            self.pinned.pop(issue_id, None)
            if self.selected_issue_id == issue_id:
                self.selected_issue_id = None
            if self.issue_id == issue_id:
                self.current = self.issue_id = None
                self.voucher_view.delete(*self.voucher_view.get_children())
                self.rows.clear()
                self.empty_detail()
                self.issue_title.set('请选择问题')
                self.view_mode = 'directory'
                self._display_issue_view()
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
        dialog.geometry('460x400')
        ttk.Label(dialog, text=f'将 {len(keys)} 张凭证加入所选问题；已有的关联会跳过。', padding=(10, 8, 10, 0)).pack(fill='x')
        ttk.Label(dialog, text='将同步加入关注；已有备注不会覆盖。', style='Hint.TLabel', padding=(10, 3, 10, 5)).pack(fill='x')
        query = tk.StringVar()
        ttk.Label(dialog, text='搜索问题名称或状态', style='Hint.TLabel', padding=(10, 0, 10, 2)).pack(fill='x')
        search = ttk.Entry(dialog, textvariable=query)
        search.pack(fill='x', padx=10, pady=(0, 5))
        choices = tk.Listbox(dialog, exportselection=False)
        choices.pack(fill='both', expand=True, padx=10)
        target_id = self.selected_issue_id if self.view_mode == 'directory' else self.issue_id
        displayed = []

        def refresh_choices(*_):
            selected = displayed[choices.curselection()[0]]['id'] if choices.curselection() else target_id
            displayed[:] = ordered_issue_choices(items, query.get(), target_id, self.recent_issue_ids)
            choices.delete(0, 'end')
            selected_index = None
            recent = set(self.recent_issue_ids)
            for index, item in enumerate(displayed):
                badge = '当前 · ' if item['id'] == target_id else '最近 · ' if item['id'] in recent else ''
                choices.insert('end', f'{badge}{item["title"]}（{item["count"]} 张）')
                if item['id'] == selected:
                    selected_index = index
            if displayed:
                selected_index = 0 if selected_index is None else selected_index
                choices.selection_set(selected_index)
                choices.activate(selected_index)
            add_button.configure(state='normal' if displayed else 'disabled')

        def add(_event=None):
            if choices.curselection() and self.add_to_issue(displayed[choices.curselection()[0]]['id'], keys):
                dialog.destroy()
            return 'break'

        def create():
            title = simpledialog.askstring('新建问题并加入', '问题名称', parent=dialog)
            if title is not None:
                try:
                    marked_before = sum(data.note_record(self.app.store, key) is not None for key in keys)
                    issue_id = data.create_issue_with_vouchers(self.app.store, title, keys)
                    attention = sum(data.note_record(self.app.store, key) is not None for key in keys) - marked_before
                    self._after_add(issue_id, keys, {'added': len(keys), 'duplicates': 0, 'attention': attention})
                    dialog.destroy()
                    self._offer_created_issue(issue_id)
                except Exception as exc:
                    messagebox.showerror('无法加入', str(exc), parent=dialog)

        footer = ttk.Frame(dialog, padding=(10, 6, 10, 10))
        footer.pack(fill='x')
        add_button = ttk.Button(footer, text='加入所选问题', style='Accent.TButton', command=add)
        add_button.pack(side='left')
        ttk.Button(footer, text='新建问题并加入…', command=create).pack(side='left', padx=6)
        ttk.Button(footer, text='取消', command=dialog.destroy).pack(side='right')
        choices.bind('<Double-1>', add)
        dialog.bind('<Return>', add)
        query.trace_add('write', refresh_choices)
        refresh_choices()
        search.focus_set()
        dialog.grab_set()

    def _offer_created_issue(self, issue_id):
        item = data.get_issue(self.app.store, issue_id)
        parent = self.window if self.detached and self.issues_visible else self.app
        dialog = tk.Toplevel(parent)
        dialog.title('问题已创建')
        dialog.transient(parent)
        ttk.Label(dialog, text=f'已创建“{item["title"]}”。可查看问题，或留在当前位置继续核查。',
                  padding=12, wraplength=360).pack(fill='x')
        actions = ttk.Frame(dialog, padding=(12, 0, 12, 12))
        actions.pack(fill='x')
        def view_issue():
            dialog.destroy()
            self.edit_issue(issue_id)
        ttk.Button(actions, text='查看问题', style='Accent.TButton', command=view_issue).pack(side='left')
        ttk.Button(actions, text='继续核查', command=dialog.destroy).pack(side='left', padx=6)
        dialog.bind('<Escape>', lambda _event: dialog.destroy())
        dialog.bind('<Return>', lambda _event: dialog.destroy())

    def add_to_issue(self, issue_id, keys):
        keys = (keys,) if isinstance(keys, str) else tuple(dict.fromkeys(keys))
        if not keys or not self.flush():
            return False
        try:
            marked_before = sum(data.note_record(self.app.store, key) is not None for key in keys)
            result = data.link_vouchers(self.app.store, issue_id, keys)
            result['attention'] = sum(data.note_record(self.app.store, key) is not None for key in keys) - marked_before
            self._after_add(issue_id, keys, result)
            return True
        except Exception as exc:
            self.feedback.set(f'加入失败：{exc}')
            return False

    def _after_add(self, issue_id, keys, result):
        if not self.issues_visible:
            self.show()
        self.recent_issue_ids = [issue_id] + [item for item in self.recent_issue_ids if item != issue_id]
        self.pinned[issue_id] = tuple(keys)
        self.refresh_directory()
        if self.view_mode == 'detail' and self.issue_id == issue_id:
            self.load_issue(issue_id)
        for key in keys:
            self.app.note_hub.broadcast(key)
            self.app.update_mark_indicators(key)
        item = data.get_issue(self.app.store, issue_id)
        self.feedback.set(link_feedback(item['title'], result))
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

    def locate_selected(self):
        keys = self.voucher_selection.action_keys()
        if len(keys) != 1:
            self.feedback.set('请选择一张凭证定位到当前明细。')
            return False
        self.app.locate_issue_voucher(keys[0])
        return True

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
        header = ttk.Frame(window, padding=10)
        header.pack(fill='x')
        ttk.Label(header, text=item['status']).pack(side='left', fill='x', expand=True)
        window.locate_button = ttk.Button(header, text='定位到当前明细',
                                          command=lambda: self.app.locate_issue_voucher(item['key']),
                                          state='normal' if item.get('entry_id') is not None else 'disabled')
        window.locate_button.pack(side='right')
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

    def _resolve_drag_target(self, x_root, y_root, keys):
        if not self.app.store or not self.issues_visible:
            return None
        if self._drag_highlight is None:
            self._drag_highlight = {'selection': tuple(self.tree.selection()),
                                    'caption_style': self.caption.cget('style')}
        widget = self.winfo_containing(x_root, y_root)
        while widget is not None and widget not in (self.tree, self.caption):
            widget = getattr(widget, 'master', None)
        issue_id = None
        node = None
        if widget is self.tree and self.view_mode == 'directory':
            node = self.tree.identify_row(y_root - self.tree.winfo_rooty())
            if node.startswith('i:'):
                issue_id = int(node[2:])
        elif widget is self.caption and self.view_mode == 'detail':
            issue_id = self.issue_id
        if issue_id not in self.issue_rows:
            selection = self._drag_highlight['selection']
            self.tree.selection_set(selection)
            self.caption.configure(style=self._drag_highlight['caption_style'])
            return None
        if node:
            self.tree.selection_set(node)
            self.tree.see(node)
        else:
            self.caption.configure(style='Title.TLabel')
        linked = {row[0] for row in self.app.store.db.execute(
            'SELECT voucher_key FROM dd_issue_vouchers WHERE issue_id=?', (issue_id,))}
        duplicates = sum(key in linked for key in keys)
        return {'issue_id': issue_id, 'title': self.issue_rows[issue_id]['title'],
                'added': len(keys) - duplicates, 'duplicates': duplicates}

    def _clear_drag_highlight(self, restore=True):
        state = self._drag_highlight
        if state is None:
            return
        if restore:
            selection = tuple(node for node in state['selection'] if self.tree.exists(node))
            self.tree.selection_set(selection)
        self.caption.configure(style=state['caption_style'])
        self._drag_highlight = None

    def _issue_menu(self, event):
        node = self.tree.identify_row(event.y)
        if not node:
            return
        issue_id = int(node[2:])
        menu = tk.Menu(self.tree, tearoff=False)
        menu.add_command(label='编辑问题记录', command=lambda: self.edit_issue(issue_id, tab='record'))
        menu.add_command(label='删除问题…', command=lambda: self.remove_issue(issue_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class VoucherActions:
    """Share explicit batch actions between ledger, marks and voucher windows."""

    def __init__(self, app):
        self.app = app
        self.sources = {}
        self.drag_bindings = {}

    def attach(self, view, key_for_row):
        self.sources[view] = key_for_row
        def clean(event):
            if event.widget is view:
                self.sources.pop(view, None)
                self.drag_bindings.pop(view, None)
        view.bind('<Destroy>', clean, add='+')
        if hasattr(view, 'cell_tools'):
            menu = view.cell_tools.menu
            menu.add_separator()
            index = menu.index('end') + 1
            menu.add_command(label='加入问题…', command=lambda: self.choose_from_view(view))
            if view is getattr(self.app, 'detail_view', None):
                menu.add_command(label='关注所选', command=lambda: self.app.mark_selected_entry())
            def labels():
                count = len(self.keys_from_view(view))
                menu.entryconfigure(index, label=f'加入问题…（{count} 张）', state='normal' if count else 'disabled')
            menu.configure(postcommand=labels)
        if hasattr(view, 'voucher_selection'):
            from .voucher_drag import VoucherDragBinding
            workbench = self.app.workbench
            self.drag_bindings[view] = VoucherDragBinding(
                view, key_for_row, lambda: self.keys_from_view(view),
                workbench._resolve_drag_target,
                lambda target, keys: workbench.add_to_issue(target['issue_id'], keys),
                workbench._clear_drag_highlight)

    def keys_from_view(self, view):
        selection = getattr(view, 'voucher_selection', None)
        if selection:
            return selection.action_keys()
        tools = getattr(view, 'cell_tools', None)
        rows = tools.selection_range()[0] if tools else ()
        return tuple(dict.fromkeys(key for row in (rows or view.selection())
                                   if (key := self.sources[view](row))))

    def choose_from_view(self, view):
        self.app.workbench.choose_issue(self.keys_from_view(view))
