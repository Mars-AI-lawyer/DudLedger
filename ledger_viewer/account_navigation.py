"""Collapsible in-page account navigation, independent of balance-table filters."""

import tkinter as tk
from tkinter import ttk


class AccountNavigation(ttk.Frame):
    ALL = "all"

    def __init__(self, parent, on_select):
        super().__init__(parent, width=230, padding=(0, 0, 8, 0))
        self.on_select = on_select
        self.accounts = {}
        self.current = self.ALL
        self.expanded = set()
        self.search = tk.StringVar()
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 5))
        ttk.Label(top, text="科目树").pack(side="left")
        ttk.Button(top, text="清除", width=4, style="Ghost.TButton", command=lambda: self.search.set("")).pack(side="right")
        self.entry = ttk.Entry(self, textvariable=self.search, width=20)
        self.entry.pack(fill="x", pady=(0, 5))
        ttk.Label(self, text="输入编码或名称，单击切换科目", style="Hint.TLabel", wraplength=205).pack(fill="x")
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, show="tree", selectmode="browse")
        self.tree.column("#0", width=210, minwidth=90, stretch=True)
        vertical = ttk.Scrollbar(holder, command=self.tree.yview)
        horizontal = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<<TreeviewOpen>>", lambda _event: self._expansion(True))
        self.tree.bind("<<TreeviewClose>>", lambda _event: self._expansion(False))
        self.search.trace_add("write", lambda *_: self.render())
        self.render()

    def set_accounts(self, rows):
        self.accounts = {row["key"]: dict(row) for row in rows}
        by_code = {row["code"]: row["key"] for row in self.accounts.values() if row["code"]}
        self.parents = {}
        for key, row in self.accounts.items():
            code = row["code"]
            self.parents[key] = next((by_code[code[:size]] for size in range(len(code) - 1, 0, -1)
                                      if code[:size] in by_code), "")
        self.render()

    def render(self):
        query = self.search.get().strip().casefold()
        keep = {key for key, row in self.accounts.items()
                if not query or query in row["code"].casefold() or query in row["name"].casefold()}
        for key in tuple(keep):
            parent = self.parents[key]
            while parent:
                keep.add(parent)
                parent = self.parents[parent]
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid=self.ALL, text="全部科目")
        # Parents precede descendants even if imported rows arrive out of order.
        def insert(key):
            if self.tree.exists(key):
                return
            row, parent = self.accounts[key], self.parents[key]
            if parent:
                insert(parent)
            self.tree.insert(parent, "end", iid=key, text=f'{row["code"]} {row["name"]}'.strip(),
                             open=bool(query) or key in self.expanded)
        for key in self.accounts:
            if key in keep:
                insert(key)
        self.sync(self.current)

    def sync(self, account):
        self.current = self.ALL if account is None else account
        if isinstance(self.current, str) and self.tree.exists(self.current):
            self.tree.selection_set(self.current)
            self.tree.see(self.current)
        else:
            self.tree.selection_remove(*self.tree.selection())

    def reset(self):
        self.current = self.ALL
        self.expanded.clear()
        self.search.set("")
        self.set_accounts([])

    def _expansion(self, opened):
        key = self.tree.focus()
        self.expanded.add(key) if opened else self.expanded.discard(key)

    def _select(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self.current:
            return
        self.current = selected[0]
        self.on_select(None if self.current == self.ALL else self.accounts[self.current])
