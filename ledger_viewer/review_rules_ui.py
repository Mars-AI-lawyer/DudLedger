"""Account and prompt editor for a single ledger's versioned review rules."""
import re
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText
from uuid import uuid4

from . import theme
from .specialist_rules import default_rules, load_rules, review_accounts, save_rules
from .window_position import center_window


class AccountNameDialog(simpledialog.Dialog):
    def __init__(self, parent, accounts):
        self.accounts = accounts
        super().__init__(parent, title='新增审查科目')

    def body(self, master):
        ttk.Label(master, text='从当前账簿选择，或直接输入科目名称').pack(anchor='w', pady=6)
        self.name = tk.StringVar()
        combo = ttk.Combobox(master, textvariable=self.name, values=sorted(self.accounts), width=36)
        combo.pack(fill='x', pady=6)
        ttk.Label(master, text='科目编码（可选，便于匹配名称不同的下级科目）').pack(anchor='w', pady=6)
        self.codes = tk.StringVar()
        ttk.Entry(master, textvariable=self.codes).pack(fill='x', pady=6)
        combo.bind('<<ComboboxSelected>>', lambda _event: self.codes.set(', '.join(self.accounts.get(self.name.get(), []))))
        return combo

    def validate(self):
        if not self.name.get().strip():
            messagebox.showinfo('请填写科目名称', '可从下拉列表选择，也可以自行输入。', parent=self)
            return False
        return True

    def apply(self):
        name = self.name.get().strip()
        self.result = name, self.codes.get().strip() or ', '.join(self.accounts.get(name, []))


class ReviewRulesDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app, self.store = app, app.store
        self.version, self.rules = load_rules(self.store)
        self.current = None
        self.title('AI审查设置 · 当前账套')
        self.transient(app)
        self.minsize(800, 560)
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        header = ttk.Frame(self, padding=12)
        header.pack(fill='x')
        ttk.Label(header, text='先选科目，再添加审查条目', style='Title.TLabel').pack(anchor='w')
        self.summary = tk.StringVar()
        ttk.Label(header, textvariable=self.summary, style='Hint.TLabel').pack(anchor='w', pady=(4, 0))
        footer = ttk.Frame(self, padding=12)
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='恢复默认', command=self._reset).pack(side='left')
        ttk.Button(footer, text='保存审查设置', style='Primary.TButton', command=self._save).pack(side='right')
        ttk.Button(footer, text='取消', command=self._cancel).pack(side='right', padx=8)
        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        left, right = ttk.Frame(split, width=260), ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)
        actions = ttk.Frame(left)
        actions.pack(fill='x', pady=(0, 8))
        ttk.Button(actions, text='＋ 新增科目', command=self._add_account).pack(side='left')
        ttk.Button(actions, text='＋ 新增条目', command=self._add_topic).pack(side='left', padx=4)
        self.outline = ttk.Treeview(left, show='tree', selectmode='browse')
        bar = ttk.Scrollbar(left, command=self.outline.yview)
        bar.pack(side='right', fill='y')
        self.outline.configure(yscrollcommand=bar.set)
        self.outline.pack(fill='both', expand=True)
        self.outline.bind('<<TreeviewSelect>>', self._choose)
        remove = ttk.Frame(left)
        remove.pack(fill='x', pady=6)
        ttk.Button(remove, text='删除条目', style='Quiet.TButton', command=self._delete).pack(side='left')
        ttk.Button(remove, text='删除科目', style='Quiet.TButton', command=lambda: self._delete(account=True)).pack(side='right')
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True, padx=(10, 0))
        content, advanced, common = (ttk.Frame(self.tabs, padding=10) for _ in range(3))
        self.tabs.add(content, text='条目内容')
        self.tabs.add(advanced, text='科目匹配')
        self.tabs.add(common, text='通用提示词')
        self.topic_label = tk.StringVar()
        self.account_label = tk.StringVar()
        ttk.Label(content, textvariable=self.account_label, style='Hint.TLabel').pack(anchor='w')
        ttk.Label(content, text='百炼、Jev、DeepSeek 共用此提示词；命中及排除条件都写在这里。',
                  style='Hint.TLabel', wraplength=450).pack(anchor='w', pady=(4, 0))
        ttk.Label(content, text='条目名称 · 用简短文字描述要查的问题').pack(anchor='w', pady=(8, 4))
        self.label_entry = ttk.Entry(content, textvariable=self.topic_label)
        self.label_entry.pack(fill='x')
        ttk.Label(content, text='审查提示词 · 写清“什么情况需要命中”').pack(anchor='w', pady=(12, 4))
        self.editor = ScrolledText(content, height=8, width=45, wrap='word', undo=True)
        self.editor.pack(fill='both', expand=True)
        ttk.Label(advanced, text='科目编码（可选，多个编码用逗号分隔）').pack(anchor='w')
        self.codes = tk.StringVar()
        ttk.Entry(advanced, textvariable=self.codes).pack(fill='x', pady=6)
        ttk.Label(advanced, text='按科目名称及下级科目匹配；名称不一致时，可填写账簿中的编码。', wraplength=450, style='Hint.TLabel').pack(anchor='w')
        ttk.Label(common, text='基础提示词 · 所有条目共用的审查边界').pack(anchor='w')
        self.common_editor = ScrolledText(common, height=8, width=45, wrap='word')
        self.common_editor.pack(fill='both', expand=True, pady=6)
        ttk.Label(common, text='信息含糊时由系统自动标记人工复核，无需另设提示词。',
                  style='Hint.TLabel', wraplength=450).pack(anchor='w', pady=(6, 0))
        ttk.Label(common, text='以上提示词由百炼、Jev、DeepSeek 共用。Jev 固定使用 1.14。',
                  style='Hint.TLabel', wraplength=450).pack(anchor='w', pady=6)
        self._load_common()
        self._rebuild(self.rules['topics'][0]['id'])
        center_window(self, app, min(1040, self.winfo_screenwidth() - 60), min(740, self.winfo_screenheight() - 100))
        self.deiconify()
        self.grab_set()

    @staticmethod
    def _put(widget, value):
        widget.delete('1.0', 'end')
        widget.insert('1.0', value)
        widget.edit_reset()
        widget.edit_modified(False)

    def _load_common(self):
        self._put(self.common_editor, self.rules['common'])

    def _stash(self):
        if self.current is None:
            return
        topic = next((t for t in self.rules['topics'] if t['id'] == self.current), None)
        if topic is None:
            return
        topic.update(label=self.topic_label.get().strip(), question=self.editor.get('1.0', 'end-1c').strip())
        codes = [s for s in re.split(r'[,，;；\s]+', self.codes.get().strip()) if s]
        if codes:
            self.rules.setdefault('account_codes', {})[topic['account']] = codes
        elif topic['account'] in self.rules.get('account_codes', {}):
            del self.rules['account_codes'][topic['account']]
        if self.outline.exists(topic['id']):
            self.outline.item(topic['id'], text=topic['label'] or '未命名条目')

    def _rebuild(self, selected=None):
        self.outline.delete(*self.outline.get_children())
        for index, name in enumerate(review_accounts(self.rules)):
            topics = [t for t in self.rules['topics'] if t['account'] == name]
            parent = self.outline.insert('', 'end', iid=f'account:{index}', text=f'{name}（{len(topics)}）', open=True, values=(name,))
            for topic in topics:
                self.outline.insert(parent, 'end', iid=topic['id'], text=topic['label'] or '未命名条目')
        self.summary.set(f'{len(review_accounts(self.rules))} 个科目 · {len(self.rules["topics"])} 个条目 · 保存后用于新审查批次，历史结果保留原规则')
        self.current = None
        if selected and self.outline.exists(selected):
            self.outline.selection_set(selected)
            self.outline.see(selected)
            self._choose()
            parent = self.outline.parent(selected)
            self.after_idle(lambda: self.outline.see(parent) if self.outline.exists(parent) else None)

    def _choose(self, _event=None):
        selection = self.outline.selection()
        if not selection:
            return
        item = selection[0]
        if item.startswith('account:'):
            children = self.outline.get_children(item)
            if children:
                self.outline.selection_set(children[0])
                self._choose()
            return
        if item == self.current:
            return
        self._stash()
        topic = next(t for t in self.rules['topics'] if t['id'] == item)
        self.current = item
        self.account_label.set('当前科目：' + topic['account'])
        self.topic_label.set(topic['label'])
        self.codes.set(', '.join(self.rules.get('account_codes', {}).get(topic['account'], [])))
        self._put(self.editor, topic['question'])

    def _add_account(self, name=None):
        codes = ''
        if name is None:
            accounts = {}
            for row in self.store.db.execute('SELECT name,code FROM accounts ORDER BY code,name'):
                if row['name'] and row['name'] not in review_accounts(self.rules):
                    values = accounts.setdefault(row['name'], [])
                    if row['code'] and row['code'] not in values:
                        values.append(row['code'])
            result = AccountNameDialog(self, accounts).result
            if result is None:
                return
            name, codes = result
        if not name or not name.strip():
            return
        name = name.strip()
        if name in review_accounts(self.rules):
            messagebox.showinfo('科目已存在', '请在左侧选择该科目，再点击“新增条目”。', parent=self)
            return
        self._add_topic(account=name)
        self.codes.set(codes)

    def _add_topic(self, account=None):
        self._stash()
        if account is None:
            topic = next((t for t in self.rules['topics'] if t['id'] == self.current), None)
            account = topic['account'] if topic else review_accounts(self.rules)[0]
        identifier = 'U' + uuid4().hex[:12]
        self.rules['topics'].append({'id': identifier, 'account': account, 'category': '自定义核查',
            'label': '', 'question': '', 'advice': ''})
        self._rebuild(identifier)
        self.tabs.select(0)
        self.label_entry.focus_set()

    def _delete(self, account=False):
        selection = self.outline.selection()
        if not selection:
            return
        item = selection[0]
        self._stash()
        topic = next(t for t in self.rules['topics'] if t['id'] == item)
        ids = {t['id'] for t in self.rules['topics'] if t['account'] == topic['account']} if account else {item}
        if len(ids) >= len(self.rules['topics']):
            messagebox.showinfo('至少保留一个条目', '请先新增其他审查条目，再删除这些内容。', parent=self)
            return
        target = '科目“' + topic['account'] + '”及其全部条目' if account else '条目“' + (topic['label'] or '未命名条目') + '”'
        if not messagebox.askyesno('删除审查设置', '删除' + target + '？保存后生效。', parent=self):
            return
        self.rules['topics'] = [t for t in self.rules['topics'] if t['id'] not in ids]
        self.rules['account_codes'] = {name: codes for name, codes in self.rules.get('account_codes', {}).items() if name in review_accounts(self.rules)}
        self._rebuild(self.rules['topics'][0]['id'])

    def _reset(self):
        if not messagebox.askyesno('恢复默认审查设置', '恢复内置科目和提示词？当前未保存的修改会被替换，保存后生效。', parent=self):
            return
        self.rules = default_rules()
        self._load_common()
        self._rebuild(self.rules['topics'][0]['id'])

    def _cancel(self):
        self._stash()
        changed = self.rules != load_rules(self.store)[1] or self.common_editor.edit_modified()
        if changed and not messagebox.askyesno('放弃修改', '审查设置尚未保存，确认放弃本次修改？', parent=self):
            return
        self.destroy()

    def _save(self):
        self._stash()
        self.rules.update(common=self.common_editor.get('1.0', 'end-1c').strip())
        try:
            save_rules(self.store, self.rules)
        except (ValueError, TypeError, AttributeError) as exc:
            messagebox.showerror('规则未保存', str(exc), parent=self)
            return
        self.app.review_panel.refresh_accounts()
        self.app.review_panel.refresh()
        self.destroy()
