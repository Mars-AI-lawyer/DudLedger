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
    def __init__(self, app):
        super().__init__(app.content)
        self.app = app
        self.visible = False
        self.mode = "issues"
        self.width = 420
        self.active_tools = None
        self.last_tools = None
        self.current = None
        self.draft = None
        self.loading_selection = False
        self.loaded = set()
        self.offsets = {}
        self.rows = {}
        self.issue_rows = {}
        self.persisted_store = None
        self.snapshot_mode = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self, padding=(8, 4))
        top.grid(row=0, column=0, sticky="ew")
        self.issue_tab = ttk.Button(top, text="问题工作台", command=self.show)
        self.issue_tab.pack(side="left")
        ttk.Button(top, text="计算器", command=self.show_calculator).pack(side="left", padx=3)
        ttk.Button(top, text="收起", command=self.hide).pack(side="right")
        self.issue_body = ttk.Frame(self)
        self.issue_body.grid(row=1, column=0, sticky="nsew")
        self.split = ttk.Panedwindow(self.issue_body, orient="vertical")
        self.split.pack(fill="both", expand=True)
        directory = ttk.Frame(self.split, padding=8)
        header = ttk.Frame(directory)
        header.pack(fill="x")
        ttk.Label(header, text="尽调问题").pack(side="left")
        ttk.Button(header, text="＋新建", command=self.new_issue).pack(side="right")
        self.preview_target = ttk.Label(directory, text="拖到这里，仅预览凭证", anchor="center", padding=10, relief="solid")
        self.preview_target.pack(fill="x", pady=(7, 6))
        tree_holder = ttk.Frame(directory)
        tree_holder.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_holder, show="tree", selectmode="browse", height=6)
        self.tree.column("#0", width=330, minwidth=80)
        self.tree.tag_configure("drop", background="#E8DECE")
        self.tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(tree_holder, command=self.tree.yview)
        bar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<<TreeviewOpen>>", self._expand)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Double-1>", self._double)
        self.tree.bind("<Button-3>", self._menu)
        self.tree.bind("<Button-2>", self._menu)
        # The upper and lower sections each scroll independently.
        detail_holder = ttk.Frame(self.split)
        self.canvas = tk.Canvas(detail_holder, highlightthickness=0, background="#FAF9F5", width=1, height=180)
        scroll = ttk.Scrollbar(detail_holder, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.detail = ttk.Frame(self.canvas, padding=10)
        self.detail_window = self.canvas.create_window(0, 0, window=self.detail, anchor="nw")
        self.detail.bind("<Configure>", self._fit_detail)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.detail_window, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.split.add(directory, weight=1)
        self.split.add(detail_holder, weight=2)
        self.sash_ratio = 0.4
        def resize_split(event):
            minimum = self.preview_target.winfo_reqheight() + header.winfo_reqheight() + 70
            position = min(max(minimum, int(event.height*self.sash_ratio)), max(80, event.height-150))
            self.split.sashpos(0, position)
        self.split.bind("<Configure>", resize_split)
        self.split.bind("<ButtonRelease-1>", lambda _e: setattr(self, "sash_ratio", self.split.sashpos(0)/max(1, self.split.winfo_height())))
        self.calc_body = ttk.Frame(self)
        self.calc_body.columnconfigure(0, weight=1)
        self.calc_body.rowconfigure(1, weight=1)
        self.calc_source = tk.StringVar(value="请先在科目总览或明细账中选择金额")
        self.calc_source_label = ttk.Label(self.calc_body, textvariable=self.calc_source, padding=8, wraplength=350)
        self.calc_source_label.grid(row=0, column=0, sticky="ew")
        self.calc_hosts = {}
        self.grip = ttk.Frame(app.content, width=5, cursor="sb_h_double_arrow")
        self.grip.bind("<B1-Motion>", self._resize)
        self.feedback = tk.StringVar(value="新建问题后，可从拖动手柄归集整张凭证。")
        ttk.Label(self, textvariable=self.feedback, style="Hint.TLabel", wraplength=360, padding=8).grid(row=2, column=0, sticky="ew")
        self.empty_detail()

    def _fit_detail(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        width = max(120, self.canvas.winfo_width()-24)
        for widget in self.detail.winfo_children():
            if isinstance(widget, ttk.Label) and int(widget.cget("wraplength") or 0):
                widget.configure(wraplength=width)

    def _flow_actions(self, holder):
        def flow(_event=None):
            widgets = holder.winfo_children()
            vertical = holder.winfo_width() < sum(widget.winfo_reqwidth()+8 for widget in widgets)
            if getattr(holder, "_vertical", None) == vertical:
                return
            holder._vertical = vertical
            for widget in widgets:
                widget.pack_forget()
                widget.pack(side="top" if vertical else "left", anchor="w", padx=(0, 4), pady=(0, 3))
        holder.bind("<Configure>", flow)

    def calculator_host(self, name):
        holder = ttk.Frame(self.calc_body)
        canvas = tk.Canvas(holder, highlightthickness=0, width=1, height=1, background="#FAF9F5")
        bar = ttk.Scrollbar(holder, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        frame = ttk.Frame(canvas)
        frame.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.calc_hosts[name] = holder
        return frame

    def set_calculator(self, tools, name, reveal=False):
        if not reveal and self.visible and self.mode == "calculator" and tools is self.active_tools:
            return self.hide()
        if not self.flush():
            return False
        self.last_tools = (tools, name)
        self.active_tools = tools
        self.mode = "calculator"
        self.issue_body.grid_remove()
        self.calc_body.grid(row=1, column=0, sticky="nsew")
        for context, frame in self.calc_hosts.items():
            if context == name:
                frame.grid(row=1, column=0, sticky="nsew")
                tools.panel.grid(row=0, column=0, sticky="nsew")
                tools.set_compact_width(None)
            else:
                frame.grid_remove()
        self.calc_source.set(f"计算来源：{name} · 与其他窗口独立")
        self.visible = True
        self.layout()
        self.app._resize_workspace()
        return True

    def show_calculator(self):
        index = self.app.tabs.index("current")
        if index in (0, 1):
            tools, name = (self.app.balance_tools, "科目总览") if index == 0 else (self.app.detail_tools, "明细账")
        elif self.last_tools:
            tools, name = self.last_tools
        else:
            tools, name = self.app.detail_tools, "明细账"
        return self.set_calculator(tools, name, reveal=True)

    def page_changed(self):
        if self.visible and self.mode == "calculator" and self.app.tabs.index("current") in (0, 1):
            self.show_calculator()

    def show(self):
        if not self.app.store:
            self.app.status.set("请先打开账套，再创建尽调问题。")
            return False
        if not self.flush():
            return False
        self.mode = "issues"
        self.calc_body.grid_remove()
        self.issue_body.grid(row=1, column=0, sticky="nsew")
        self.visible = True
        if self.persisted_store is not self.app.store:
            self.reset()
        self.refresh_directory()
        self.layout()
        self.app._resize_workspace()
        return True

    def hide(self):
        if not self.flush():
            return False
        self.visible = False
        if self.active_tools is not None:
            self.active_tools.panel.grid_remove()
        self.layout()
        self.app._show_sidebar_with_calculator = False
        self.app._resize_workspace()
        return True

    def layout(self):
        if not self.visible:
            self.grid_remove()
            self.place_forget()
            self.grip.grid_remove()
            self.grip.place_forget()
            return
        available = self.app.content.winfo_width()
        width = min(self.width, max(320, available - 20))
        self.configure(width=width)
        if available >= width + 500:
            self.place_forget()
            self.grip.place_forget()
            self.grip.grid(row=0, column=1, sticky="ns")
            self.grid(row=0, column=2, sticky="nsew")
            self.grid_propagate(False)
        else:
            self.grid_remove()
            self.grip.grid_remove()
            self.place(relx=1, x=0, y=0, anchor="ne", width=width, relheight=1)
            self.grip.place(relx=1, x=-width-5, y=0, width=5, relheight=1)
            self.lift()
            self.grip.lift()
        self.feedback_label_wrap(width)

    def feedback_label_wrap(self, width):
        self.calc_source_label.configure(wraplength=max(180, width-24))
        for widget in self.grid_slaves(row=2):
            widget.configure(wraplength=max(180, width - 24))

    def _resize(self, event):
        self.width = max(320, min(650, self.app.content.winfo_rootx() + self.app.content.winfo_width() - event.x_root))
        self.layout()

    def reset(self):
        self.current = None
        self.draft = None
        self.persisted_store = self.app.store
        self.loaded.clear()
        self.offsets.clear()
        self.rows.clear()
        self.issue_rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.empty_detail()
        if self.app.store:
            self.refresh_directory()

    def flush(self):
        if self.draft and not self.draft.flush():
            self.app.status.set(self.feedback.get())
            return False
        return self.app.note_hub.flush()

    def refresh_directory(self):
        if not self.app.store:
            return
        issues = data.list_issues(self.app.store)
        ids = {f'i:{item["id"]}' for item in issues}
        for node in self.tree.get_children():
            if node not in ids:
                self._forget_node(node)
        for item in issues:
            node = f'i:{item["id"]}'
            label = f'{item["title"]}  ·  {item["count"]}张  ·  {item["status"]}'
            self.issue_rows[item["id"]] = item
            if self.tree.exists(node):
                self.tree.item(node, text=label)
            else:
                self.tree.insert("", "end", iid=node, text=label)
            if item["count"] and not self.tree.get_children(node):
                self.tree.insert(node, "end", text="展开查看凭证", tags=("placeholder",))
        if not issues:
            self.feedback.set("点击“＋新建”，输入你要核查的问题。")

    def _forget_node(self, node):
        for child in self.tree.get_children(node):
            self.rows.pop(child, None)
        self.tree.delete(node)
        self.loaded.discard(node)

    def load_issue(self, issue_id, more=False):
        node = f"i:{issue_id}"
        if not self.tree.exists(node):
            return
        offset = self.offsets.get(issue_id, 0) if more else 0
        for child in self.tree.get_children(node):
            if not more or child not in self.rows:
                self.rows.pop(child, None)
                self.tree.delete(child)
        batch = data.issue_vouchers(self.app.store, issue_id, offset)
        self.offsets[issue_id] = offset + len(batch)
        for index, item in enumerate(batch, offset):
            if (issue_id, item["voucher_key"]) in self.rows.values():
                continue
            iid = f"v:{issue_id}:{index}"
            self.rows[iid] = (issue_id, item["voucher_key"])
            self.tree.insert(node, "end", iid=iid, text=f'⋮⋮ {item["label"]} · {item["summary"][:45]}')
        if self.offsets[issue_id] < self.issue_rows[issue_id]["count"]:
            self.tree.insert(node, "end", iid=f"more:{issue_id}", text="加载更多凭证…")
        self.loaded.add(node)

    def _expand(self, _event=None):
        node = self.tree.focus()
        if node.startswith("i:") and node not in self.loaded:
            self.load_issue(int(node[2:]))

    def _select(self, _event=None):
        if self.loading_selection:
            return
        selected = self.tree.selection()
        if not selected:
            return
        node = selected[0]
        if node.startswith("more:"):
            self.load_issue(int(node[5:]), more=True)
            return
        desired = None
        if node.startswith("i:"):
            desired = ("issue", int(node[2:]))
        elif node in self.rows:
            issue_id, key = self.rows[node]
            desired = ("voucher", key, issue_id)
        if desired is not None and self.current == desired:
            self.selected_node = node
            return
        previous = getattr(self, "selected_node", None)
        if not self.flush():
            if previous and self.tree.exists(previous):
                self.loading_selection = True
                self.tree.selection_set(previous)
                self.loading_selection = False
            return
        self.selected_node = node
        if node.startswith("i:"):
            self.edit_issue(int(node[2:]))
        elif node in self.rows:
            issue_id, key = self.rows[node]
            self.preview(key, issue_id)

    def empty_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        self.draft = None
        self.canvas.yview_moveto(0)
        ttk.Label(self.detail, text="选择问题写核查笔记，或拖入凭证查看详情。", wraplength=300, style="Hint.TLabel").pack(fill="x")

    def _clear_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        self.draft = None
        self.canvas.yview_moveto(0)
        self.editor_owner = ttk.Frame(self.detail)
        self.editor_owner.pack()

    def new_issue(self):
        if not self.app.store or not self.flush():
            return
        title = simpledialog.askstring("新建尽调问题", "问题名称", parent=self.app)
        if title is None:
            return
        try:
            issue_id = data.create_issue(self.app.store, title)
        except Exception as exc:
            messagebox.showerror("无法创建问题", str(exc), parent=self.app)
            return
        self.show()
        self.refresh_directory()
        self.tree.selection_set(f"i:{issue_id}")
        self.tree.focus(f"i:{issue_id}")
        self.edit_issue(issue_id)

    def edit_issue(self, issue_id):
        if not self.flush():
            return False
        item = data.get_issue(self.app.store, issue_id)
        self._clear_detail()
        self.current = ("issue", issue_id)
        node = f"i:{issue_id}"
        if self.tree.exists(node):
            self.selected_node = node
            self.tree.selection_set(node)
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
            self.refresh_directory()
        self.draft = Draft(self.editor_owner, capture, save, self.feedback)
        self.title_var.trace_add("write", self.draft.changed)
        self.status_var.trace_add("write", self.draft.changed)
        for widget in (title, status, self.description, self.notes):
            self.draft.watch(widget)
        buttons = ttk.Frame(self.detail)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存", command=self.draft.flush).pack(side="left")
        ttk.Button(buttons, text="重读已保存内容…", command=lambda: self.reload_issue(issue_id)).pack(side="left", padx=4)
        ttk.Button(buttons, text="删除问题", command=lambda: self.remove_issue(issue_id)).pack(side="right")
        self._flow_actions(buttons)
        self.feedback.set("已保存")
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
            self.current = None
            self.empty_detail()
            self.refresh_directory()

    def preview(self, key, issue_id=None):
        if not self.flush():
            return False
        try:
            item = data.voucher_preview(self.app.store, key)
        except Exception as exc:
            self.feedback.set(str(exc))
            return False
        self._clear_detail()
        self.current = ("voucher", key, issue_id)
        node = next((node for node, pair in self.rows.items() if pair == (issue_id, key)), None)
        if node and self.tree.exists(node):
            self.selected_node = node
            self.tree.selection_set(node)
            self.tree.see(node)
        else:
            self.selected_node = None
            self.tree.selection_remove(*self.tree.selection())
        self.preview_item = item
        self.snapshot_mode = False
        if issue_id is not None:
            ttk.Button(self.detail, text="返回问题笔记", command=lambda: self.edit_issue(issue_id)).pack(anchor="w", pady=(0, 6))
        ttk.Label(self.detail, text=item["label"], font=("", 12, "bold"), wraplength=340).pack(fill="x")
        ttk.Label(self.detail, text=item["status"], wraplength=340, foreground="#A65036" if item["status"] != "正常" else "#77766F").pack(fill="x", pady=4)
        self.lines_text = tk.Text(self.detail, height=9, width=1, wrap="word")
        self.lines_text.pack(fill="x", pady=5)
        self._render_preview_lines(item["lines"])
        if item["snapshot"] and item["entry_id"] is not None and item["status"] != "正常":
            ttk.Button(self.detail, text="切换当前分录／归集时快照", command=self.toggle_snapshot).pack(anchor="w")
            ttk.Button(self.detail, text="确认已复核当前内容", command=lambda: self.confirm(key)).pack(anchor="w", pady=4)
        actions = ttk.Frame(self.detail)
        actions.pack(fill="x", pady=5)
        open_button = ttk.Button(actions, text="打开完整凭证", command=lambda: self.app.open_voucher(None, entry_id=item["entry_id"]))
        open_button.pack(side="left")
        locate = ttk.Button(actions, text="定位明细账", command=lambda: self.app.locate_issue_voucher(key))
        locate.pack(side="left", padx=4)
        if item["entry_id"] is None:
            open_button.state(["disabled"])
            locate.state(["disabled"])
        actions2 = ttk.Frame(self.detail)
        actions2.pack(fill="x", pady=(0, 8))
        ttk.Button(actions2, text="加入问题…", command=lambda: self.choose_issue(key)).pack(side="left")
        if issue_id is not None:
            ttk.Button(actions2, text="移除关联", command=lambda: self.remove_link(issue_id, key)).pack(side="left", padx=4)
        self._flow_actions(actions)
        self._flow_actions(actions2)
        ttk.Label(self.detail, text="凭证关注备注（多问题共用）").pack(anchor="w")
        note = tk.Text(self.detail, height=4, width=1, wrap="word", undo=True)
        note.pack(fill="x", pady=4)
        self.app.note_hub.bind(self.editor_owner, note, key, self.feedback, on_saved=lambda: self.app.update_mark_indicators(key))
        ttk.Button(self.detail, text="保存备注", command=self.app.note_hub.flush).pack(anchor="e")
        ttk.Button(self.detail, text="重读已保存备注…", command=lambda: self.app.note_hub.reload(note)).pack(anchor="e", pady=3)
        self.feedback.set("已保存；输入备注将保存至关注记录。")
        return True

    def _render_preview_lines(self, lines):
        self.lines_text.configure(state="normal")
        self.lines_text.delete("1.0", "end")
        for line in lines:
            extra = " · ".join(f"{label}：{line[field]}" for field, label in
                               (("counterparty", "往来单位"), ("auxiliary", "辅助核算"),
                                ("original_amount", "原币金额"), ("maker", "制单人")) if line.get(field))
            self.lines_text.insert("end", f'{line["entry_date"]} · {line["code"]} {line["name"]}\n{line["summary"]}\n'
                f'借方 {format_money(line["debit_cents"], comma=True)}  贷方 {format_money(line["credit_cents"], comma=True)}'
                f'  {line["currency"]}\n' + (extra + "\n" if extra else "") +
                f'来源：{line["original_path"]} / {line["sheet"]} : {line["row_no"]}\n\n')
        self.lines_text.configure(state="disabled")

    def toggle_snapshot(self):
        self.snapshot_mode = not self.snapshot_mode
        self._render_preview_lines(self.preview_item["snapshot"] if self.snapshot_mode else self.preview_item["lines"])
        self.feedback.set("正在查看归集时快照" if self.snapshot_mode else "正在查看当前分录")

    def confirm(self, key):
        if self.flush() and messagebox.askyesno("确认复核", "已核对当前分录与归集时快照？确认后更新所有问题共用的凭证快照。", parent=self.app):
            data.confirm_reference(self.app.store, key)
            self.preview(key, self.current[2])

    def remove_link(self, issue_id, key):
        if not self.flush():
            return
        data.unlink_voucher(self.app.store, issue_id, key)
        self.refresh_directory()
        self.load_issue(issue_id)
        self.edit_issue(issue_id)
        self.feedback.set("已移除关联，凭证关注保留。")

    def choose_issue(self, key):
        if not self.app.store or not self.flush():
            return
        issues = data.list_issues(self.app.store)
        dialog = tk.Toplevel(self.app)
        dialog.title("加入尽调问题")
        dialog.transient(self.app)
        dialog.geometry("400x300")
        ttk.Label(dialog, text="选择问题；同一凭证可以加入多个问题。", padding=10).pack(fill="x")
        choices = tk.Listbox(dialog, exportselection=False)
        choices.pack(fill="both", expand=True, padx=10)
        for item in issues:
            choices.insert("end", item["title"])
        def add():
            if choices.curselection():
                self.add_to_issue(issues[choices.curselection()[0]]["id"], key)
                dialog.destroy()
        ttk.Button(dialog, text="加入所选问题", command=add).pack(pady=8)
        def create():
            title = simpledialog.askstring("新建问题并加入", "问题名称", parent=dialog)
            if title is not None:
                try:
                    issue_id = data.create_issue(self.app.store, title)
                    if self.add_to_issue(issue_id, key):
                        dialog.destroy()
                except Exception as exc:
                    messagebox.showerror("无法加入", str(exc), parent=dialog)
        ttk.Button(dialog, text="新建问题并加入…", command=create).pack(pady=(0, 8))
        choices.bind("<Double-1>", lambda _e: add())
        dialog.grab_set()

    def add_to_issue(self, issue_id, key):
        if not self.flush():
            return False
        try:
            added = data.link_voucher(self.app.store, issue_id, key)
            self.show()
            self.refresh_directory()
            self.load_issue(issue_id)
            # Pin a distant match without eagerly loading thousands of directory rows.
            if (issue_id, key) not in self.rows.values():
                reference = data.reference_summary(self.app.store, key)
                selected = f"v:{issue_id}:selected"
                self.rows[selected] = (issue_id, key)
                self.tree.insert(f"i:{issue_id}", 0, iid=selected, text=f'⋮⋮ {reference["label"]} · {reference["summary"][:45]}')
            node = f"i:{issue_id}"
            self.tree.item(node, open=True)
            target = next(row for row, pair in self.rows.items() if pair == (issue_id, key))
            self.loading_selection = True
            self.tree.selection_set(target)
            self.tree.see(target)
            self.selected_node = target
            self.loading_selection = False
            self.preview(key, issue_id)
            self.app.note_hub.broadcast(key)
            self.app.update_mark_indicators(key)
            self.feedback.set("已加入问题" if added else "该问题已包含此凭证")
            return True
        except Exception as exc:
            self.feedback.set(f"加入失败：{exc}")
            return False

    def _double(self, event):
        node = self.tree.identify_row(event.y)
        if node in self.rows:
            item = data.voucher_preview(self.app.store, self.rows[node][1])
            if item["entry_id"] is not None:
                self.app.open_voucher(None, entry_id=item["entry_id"])
            return "break"

    def _menu(self, event):
        node = self.tree.identify_row(event.y)
        menu = tk.Menu(self, tearoff=False)
        if node in self.rows:
            issue_id, key = self.rows[node]
            menu.add_command(label="加入其他问题…", command=lambda: self.choose_issue(key))
            menu.add_command(label="移除关联", command=lambda: self.remove_link(issue_id, key))
        elif node.startswith("i:"):
            issue_id = int(node[2:])
            menu.add_command(label="编辑问题", command=lambda: self.edit_issue(issue_id))
            menu.add_command(label="删除问题", command=lambda: self.remove_issue(issue_id))
        else:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class VoucherDrag:
    """Dedicated bindtag prevents native row/cell dragging from consuming handles."""

    def __init__(self, app):
        self.app = app
        self.tag = f"VoucherDrag:{id(self)}"
        self.sources = {}
        self.pending = None
        self.active = False
        self.tip = None
        self.hover_job = None
        self.hover_target = None
        self.highlight = None
        self.restore_hidden = False
        app.bind_class(self.tag, "<ButtonPress-1>", self.press)
        app.bind_class(self.tag, "<B1-Motion>", self.motion)
        app.bind_class(self.tag, "<ButtonRelease-1>", self.release)
        app.bind_class(self.tag, "<Escape>", self.cancel)

    def attach(self, view, key_for_row, tree_handle=True):
        self.sources[view] = key_for_row
        view.bindtags((self.tag, *view.bindtags()))
        if tree_handle:
            view.configure(show="tree headings")
            view.column("#0", width=30, minwidth=30, stretch=False)
            view.heading("#0", text="⋮⋮")
        def clean(event):
            if event.widget is view:
                if self.pending and self.pending[0] is view:
                    self.cancel()
                self.sources.pop(view, None)
        view.bind("<Destroy>", clean, add="+")
        if hasattr(view, "cell_tools"):
            view.cell_tools.menu.add_separator()
            view.cell_tools.menu.add_command(label="加入问题…", command=lambda: self.choose_from_view(view))
            view.cell_tools.menu.add_command(label="在工作台预览", command=lambda: self.preview_from_view(view))
        elif tree_handle:
            def context(event):
                row = view.identify_row(event.y)
                if not row:
                    return
                menu = tk.Menu(view, tearoff=False)
                menu.add_command(label="加入问题…", command=lambda: self.app.workbench.choose_issue(key_for_row(row)))
                menu.add_command(label="在工作台预览", command=lambda: self._preview(key_for_row(row)))
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()
            for button in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
                view.bind(button, context, add="+")

    def choose_from_view(self, view):
        selected = view.selection()
        if selected:
            self.app.workbench.choose_issue(self.sources[view](selected[0]))

    def preview_from_view(self, view):
        selected = view.selection()
        if selected:
            self._preview(self.sources[view](selected[0]))

    def _preview(self, key):
        if key and self.app.workbench.show():
            self.app.workbench.preview(key)

    def press(self, event):
        view = event.widget
        if not self.app.store or view not in self.sources:
            return
        row = view.identify_row(event.y)
        if view is self.app.workbench.tree:
            # Leave the native disclosure arrow usable; the textual grip starts dragging.
            box = view.bbox(row) if row else ()
            handle = row in self.app.workbench.rows and box and event.x > box[0] + 18 and event.x < box[0] + 55
        else:
            handle = view.identify_column(event.x) == "#0" and view.identify_region(event.x, event.y) != "separator"
        if not row or not handle:
            return
        try:
            key = self.sources[view](row)
        except (ValueError, KeyError, TypeError):
            return "break"
        if key:
            self.restore_hidden = False
            self.original_calculator = None
            self.pending = (view, key, event.x_root, event.y_root)
            view.focus_set()
        return "break"

    def motion(self, event):
        if not self.pending:
            return
        view, key, start_x, start_y = self.pending
        if not self.active and abs(event.x_root-start_x) + abs(event.y_root-start_y) < 6:
            return "break"
        if not self.active:
            self.active = True
            self.restore_hidden = not self.app.workbench.visible
            self.original_calculator = self.app.workbench.last_tools if self.app.workbench.visible and self.app.workbench.mode == "calculator" else None
            self.tip = tk.Toplevel(self.app)
            self.tip.overrideredirect(True)
            self.tip.attributes("-topmost", True)
            self.tip_label = ttk.Label(self.tip, text="拖动凭证", padding=6, relief="solid")
            self.tip_label.pack()
            view.grab_set()
        target = self.target_at(event.x_root, event.y_root)
        label = "松开取消"
        if target == "preview":
            label = "仅预览凭证"
        elif isinstance(target, int):
            label = "加入：" + self.app.workbench.issue_rows[target]["title"]
        self.tip_label.configure(text=f"{self.short_label(key)} · {label}")
        self.tip.geometry(f"+{event.x_root+16}+{event.y_root+18}")
        self._hover(target, event.x_root, event.y_root)
        return "break"

    def short_label(self, key):
        import json
        parts = json.loads(key)
        return " ".join(str(part) for part in parts[1:])[-45:]

    def target_at(self, x, y):
        panel = self.app.workbench
        if not panel.visible or panel.mode != "issues":
            return None
        def contains(widget):
            return widget.winfo_ismapped() and widget.winfo_rootx() <= x < widget.winfo_rootx()+widget.winfo_width() and widget.winfo_rooty() <= y < widget.winfo_rooty()+widget.winfo_height()
        if contains(panel.preview_target):
            return "preview"
        if contains(panel.tree):
            node = panel.tree.identify_row(y-panel.tree.winfo_rooty())
            if node.startswith("i:"):
                return int(node[2:])
            if node in panel.rows:
                return panel.rows[node][0]
        return None

    def _hover(self, target, x, y):
        panel = self.app.workbench
        edge = self.app.winfo_rootx()+self.app.winfo_width()-24 <= x <= self.app.winfo_rootx()+self.app.winfo_width() and self.app.winfo_rooty() <= y <= self.app.winfo_rooty()+self.app.winfo_height()
        hover = ("edge" if edge and (not panel.visible or panel.mode != "issues") else target)
        if hover != self.hover_target:
            if self.hover_job:
                self.app.after_cancel(self.hover_job)
                self.hover_job = None
            if self.highlight and panel.tree.exists(self.highlight):
                panel.tree.item(self.highlight, tags=())
            self.highlight = f"i:{target}" if isinstance(target, int) else None
            if self.highlight:
                panel.tree.item(self.highlight, tags=("drop",))
            panel.preview_target.configure(relief="ridge" if target == "preview" else "solid")
            self.hover_target = hover
            if hover == "edge":
                self.hover_job = self.app.after(450, self._open_edge)
            elif isinstance(hover, int):
                self.hover_job = self.app.after(450, lambda: self._open_issue(hover))
        if panel.tree.winfo_ismapped() and panel.tree.winfo_rootx() <= x <= panel.tree.winfo_rootx()+panel.tree.winfo_width():
            local = y-panel.tree.winfo_rooty()
            if 0 <= local < 18:
                panel.tree.yview_scroll(-1, "units")
            elif panel.tree.winfo_height()-18 < local < panel.tree.winfo_height():
                panel.tree.yview_scroll(1, "units")

    def _open_edge(self):
        self.hover_job = None
        if self.active:
            self.app.workbench.show()

    def _open_issue(self, issue_id):
        self.hover_job = None
        if self.active:
            panel = self.app.workbench
            panel.load_issue(issue_id)
            panel.tree.item(f"i:{issue_id}", open=True)

    def release(self, event):
        if not self.pending:
            return
        key = self.pending[1]
        target = self.target_at(event.x_root, event.y_root) if self.active else None
        was_active = self.active
        self._cleanup()
        success = False
        if was_active and target == "preview":
            success = self.app.workbench.preview(key)
        elif was_active and isinstance(target, int):
            success = self.app.workbench.add_to_issue(target, key)
        if not success:
            self._restore_panel()
        return "break"

    def cancel(self, _event=None):
        if self.pending:
            self._cleanup()
            self._restore_panel()
            return "break"

    def _restore_panel(self):
        if self.restore_hidden:
            self.app.workbench.hide()
        elif getattr(self, "original_calculator", None):
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
        panel.preview_target.configure(relief="solid")
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
