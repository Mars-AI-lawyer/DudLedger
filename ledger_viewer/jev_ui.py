"""Tk review pages; all network work runs outside the GUI thread."""
from __future__ import annotations

import json
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from queue import Empty, Queue
from threading import Thread

from .background import check_cancelled
from .store import LedgerStore
from .importer import format_money
from .jev_mapping import get_api_key
from .deepseek_mapping import get_api_key as get_deepseek_key
from .bailian_review import get_api_key as get_bailian_key, DEFAULT_ENDPOINT as BAILIAN_ENDPOINT
from .specialist_rules import ACCOUNTS, default_rules, load_rules, save_rules
from .related_parties import party_names, role_grouped_names
from .jev_review import (BOUNDARY, STATUS_LABELS, SEND_FIELDS,
    ReviewJob, active_job, batch_counts, batch_info, discard_preview,
    is_uncertain, prepare_batch, preview_page, recover_interrupted,
    refresh_staleness, save_feedback, test_connection, voucher_results, voucher_result_context)

CONSENT_TEXT = ('本次将把所选账簿记录中的日期、科目、摘要、往来单位、辅助核算、非零一侧的借贷金额，'
    '以及当前账套尽调主体名单中的名称与身份（不含备注），默认依次使用阿里百炼决策模型、TypeSafe Jev、DeepSeek Flash（不思考）；可在设置中改为 Jev 优先，前一级未配置或余额不足时尝试下一级，'
    '用于筛选值得法律尽调律师进一步查看的凭证。\n\n'
    '同时提供本次启用的专项问题及明确取得的历史背景。'
    '这些字段可能包含客户商业信息或个人信息。请确认本次使用符合项目的保密安排，并仅选择本次审查所需的范围。'
    '先按所选科目定位凭证，再发送同一凭证的完整分录作为上下文；不会因此扫描银行存款等其他科目的全部凭证。'
    '部分凭证附带同名往来单位90日内同币种、同金额的历史候选（最多20条，不证明资金流转或长期挂账）。'
    '原始账簿文件不会上传，具体发送内容可在下方逐页预览。\n\n'
    '三种模型均逐项按提示词判断命中或不命中，不另设分档、概率阈值或金额占比筛选。最终由律师核查。')


def wrapped_label(parent, **options):
    label = ttk.Label(parent, **options)
    label.bind('<Configure>', lambda event: label.configure(wraplength=max(180, event.width - 24)))
    return label


def read_only_text(parent, text, height=12):
    widget = ScrolledText(parent, wrap='word', height=height, padx=8, pady=6)
    widget.pack(fill='both', expand=True)
    widget.insert('1.0', text)
    widget.configure(state='disabled')
    return widget


def display_input(payload):
    # Optional fields (counterparty/auxiliary/amounts) are omitted when blank or zero.
    lines = [f'{label}：{payload[field]}' for field, label in SEND_FIELDS.items() if payload.get(field)]
    return '\n'.join(lines) if lines else '（无内容）'


def launch_connection_test(parent, status, *, provider='jev', api_key=None, endpoint=BAILIAN_ENDPOINT):
    key = api_key if api_key is not None else get_api_key()
    if not key:
        messagebox.showinfo('尚未设置密钥', '请先输入并保存 API Key。', parent=parent)
        return
    if getattr(parent, '_connection_testing', False):
        return
    parent._connection_testing = True
    queue = Queue()
    status.set('正在使用固定虚构内容测试连接…')
    def work():
        try:
            if provider == 'bailian':
                from .bailian_review import test_connection as test_bailian
                result = test_bailian(key, endpoint)
            else:
                result = test_connection(key)
            queue.put('连接成功（仅发送虚构样例）' if result['status'] == 'success' else result.get('error', '连接测试取消'))
        except Exception:
            queue.put('连接测试失败，请检查网络与密钥')
    Thread(target=work, daemon=True).start()
    def poll():
        if not parent.winfo_exists():
            return
        try:
            status.set(queue.get_nowait())
            parent._connection_testing = False
        except Empty:
            parent.after(150, poll)
    parent.after(150, poll)


class ReviewRulesDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.store = app.store
        self.title('AI审查设置 · 当前账套')
        self.geometry('820x720')
        self.minsize(660, 560)
        self.transient(app)
        self.version, self.rules = load_rules(self.store)
        ttk.Label(self, text=f'当前专项规则 v{self.version} · 6 类科目 / 30 项问题 · 保存生成新版本，历史结果保留原规则', padding=10).pack(anchor='w')
        model_row = ttk.Frame(self, padding=(10, 0))
        model_row.pack(fill='x')
        ttk.Label(model_row, text='Jev 模型').pack(side='left')
        self.model = tk.StringVar(value=self.rules['model'])
        ttk.Combobox(model_row, textvariable=self.model, values=('jev-1.13.0', 'jev-1.14.0'), state='readonly', width=16).pack(side='left', padx=8)
        ttk.Label(model_row, text='默认百炼优先；可在偏好设置改为 Jev 优先').pack(side='left')
        ttk.Label(self, text='逐项按照提示词直接判断命中或不命中，结果由律师核查。', padding=10).pack(anchor='w')
        self.paths = {'基础提示词（三种模型共用）': ('common',), '信息不足提问（百炼／Jev）': ('insufficient',)}
        for i, topic in enumerate(self.rules['topics']):
            title = f"{topic['account']} / {topic['id']} {topic['label']}"
            self.paths[f"{title} · 提问"] = ('topics', i, 'question')
            self.paths[f"{title} · 核查建议"] = ('topics', i, 'advice')
            if topic.get('bailian_criteria'):
                self.paths[f"{title} · 百炼命中条件"] = ('topics', i, 'bailian_criteria', 'true')
                self.paths[f"{title} · 百炼排除条件"] = ('topics', i, 'bailian_criteria', 'false')
        self.selection = tk.StringVar(value=next(iter(self.paths)))
        combo = ttk.Combobox(self, textvariable=self.selection, values=list(self.paths), state='readonly')
        combo.pack(fill='x', padx=10, pady=5)
        self.editor = ScrolledText(self, wrap='word', undo=True, padx=8, pady=8)
        self.editor.pack(fill='both', expand=True, padx=10)
        self.current = self.selection.get()
        self.editor.insert('1.0', self._value(self.paths[self.current]))
        combo.bind('<<ComboboxSelected>>', self._switch)
        actions = ttk.Frame(self, padding=10)
        actions.pack(side='bottom', fill='x', before=self.editor.frame)
        ttk.Button(actions, text='恢复默认（保存后生效）', command=self._reset).pack(side='left')
        ttk.Button(actions, text='保存为新版本', command=self._save).pack(side='right')
        ttk.Button(actions, text='取消', command=self.destroy).pack(side='right', padx=8)
        self.grab_set()

    def _value(self, path):
        value = self.rules
        for field in path:
            value = value[field]
        return value

    def _stash(self):
        path = self.paths[self.current]
        target = self.rules
        for field in path[:-1]:
            target = target[field]
        target[path[-1]] = self.editor.get('1.0', 'end-1c')

    def _switch(self, _event=None):
        self._stash()
        self.current = self.selection.get()
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', self._value(self.paths[self.current]))

    def _reset(self):
        self.rules = default_rules()
        self.model.set(self.rules['model'])
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', self._value(self.paths[self.current]))

    def _save(self):
        self._stash()
        try:
            self.rules['model'] = self.model.get()
            save_rules(self.store, self.rules)
        except (ValueError, TypeError, AttributeError) as exc:
            messagebox.showerror('规则未保存', str(exc), parent=self)
            return
        self.app.review_panel.refresh()
        self.destroy()


class ConsentDialog(tk.Toplevel):
    def __init__(self, panel, batch_id):
        super().__init__(panel)
        self.panel, self.store, self.batch_id = panel, panel.app.store, batch_id
        self.page = 0
        counts = batch_counts(self.store, batch_id)
        self.total = counts['total']
        self.voucher_total = counts['vouchers']
        self.title('AI 专项抽查 · 发送预览')
        self.geometry('810x730')
        self.minsize(670, 560)
        self.transient(panel.app)
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        info = batch_info(self.store, batch_id)
        scope = json.loads(info['scope_json'])
        description = []
        labels = {'account': '科目', 'search': '关键词', 'fiscal_year': '年度', 'period': '期间', 'source_ids': '来源编号', 'include_descendants': '包含下级科目', 'review_accounts': '专项科目', 'bailian_endpoint': '百炼接口'}
        for field, value in scope.items():
            if value and field in labels:
                description.append(f'{labels[field]}：' + ('、'.join(value) if isinstance(value, list) else str(value)))
        wrapped_label(self, text='本次将选定凭证及其完整分录、尽调主体名单和程序特征发送至下列模型服务；可能包含客户商业信息或个人信息，请确认符合项目保密安排。原始文件不上传。完整说明、名单及实际发送字段见下方预览。', wraplength=750, padding=12).pack(fill='x')
        order = 'Jev → 百炼 → DeepSeek Flash' if scope.get('preferred_provider') == 'jev' else '百炼 → Jev → DeepSeek Flash'
        wrapped_label(self, text='本次优先顺序：' + order, padding=(12, 0)).pack(fill='x')
        wrapped_label(self, text=f'账套：{self.store.path.name}\n范围：' + ('；'.join(description) or '全部明细分录') + f'\n共 {self.total} 条分录，归为 {self.voucher_total} 张凭证／无号来源行；每张凭证去重合并后核查（相同行合并金额并标注次数，仅发送非零一侧金额）；超长内容分段 · 规则 v{info["rule_id"]} · {info["model"]}', padding=(12, 4), wraplength=750).pack(fill='x')
        parties = party_names(self.store.related_parties())
        grouped = role_grouped_names(self.store.related_parties())
        self.party_payload = grouped
        wrapped_label(self, text='发送字段：' + '、'.join(SEND_FIELDS.values())
            + f'；尽调主体名单共 {len(parties)} 个名称（仅名称、别名和身份）。文件名、路径、制单人及凭证标识留在本地。', padding=(12, 4), wraplength=750).pack(fill='x')
        wrapped_label(self, text=f"已排除结转损益凭证 {counts['skipped_vouchers']} 张；仅对本次专项科目的凭证提问，不使用整体风险判断。", padding=(12, 0)).pack(fill='x')
        self.preview = read_only_text(self, '', height=13)
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side='bottom', fill='x', before=self.preview.frame)
        ttk.Button(bottom, text='上一页', command=lambda: self._page(-1)).pack(side='left')
        self.page_label = tk.StringVar()
        ttk.Label(bottom, textvariable=self.page_label).pack(side='left', padx=8)
        ttk.Button(bottom, text='下一页', command=lambda: self._page(1)).pack(side='left')
        self.confirm = ttk.Button(bottom, text='确认并开始审查', command=self._confirm)
        self.confirm.pack(side='right')
        cancel = ttk.Button(bottom, text='取消', command=self._cancel)
        cancel.pack(side='right', padx=8)
        self._page(0)
        self.grab_set()
        self.after_idle(cancel.focus_set)
        self.bind('<Escape>', lambda _e: self._cancel())

    def _page(self, delta):
        self.page = min(max(0, self.page + delta), (self.total - 1) // 20)
        rows = preview_page(self.store, self.batch_id, self.page)
        blocks, seen = [], set()
        if self.page == 0:
            blocks.extend([CONSENT_TEXT, '本次尽调主体名单：\n' + json.dumps(self.party_payload, ensure_ascii=False, indent=2)])
        for index, row in enumerate(rows, 1):
            blocks.append(f'第 {self.page * 20 + index} 条' + ('（结转损益，不发送）' if row['status'] == 'skipped' else '') + f'\n{display_input(json.loads(row["input_json"]))}')
            if row['features_json'] and row['voucher_key'] not in seen:
                seen.add(row['voucher_key'])
                feature = json.loads(row['features_json'])
                blocks.append('程序特征及历史背景：\n' + json.dumps({k: feature[k] for k in ('facts', 'background', 'review_accounts', 'check_ids') if k in feature}, ensure_ascii=False, indent=2))
        text = '\n\n'.join(blocks)
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')
        self.page_label.set(f'{self.page + 1} / {(self.total + 19) // 20}')

    def _cancel(self):
        discard_preview(self.store, self.batch_id)
        self.destroy()

    def _confirm(self):
        try:
            job = ReviewJob(self.store.path, self.batch_id, get_api_key(), deepseek_key=get_deepseek_key(), bailian_key=get_bailian_key())
            job.start(self.store)
        except Exception as exc:
            messagebox.showerror('未开始审查', str(exc), parent=self)
            return
        self.panel.batch_id = self.batch_id
        self.panel.job = job
        self.panel.lane.set('candidates')
        self.panel.account.set('')
        self.panel.category.set('全部')
        self.panel.period.set('全部期间')
        self.panel.refresh()
        self.destroy()


class ReviewPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        from .ui import tree
        self.app = app
        self.batch_id = None
        self.preparing = False
        self._totals_cache = None
        self._stale_token = None
        self._last_refresh = 0
        self._rows_dirty = False
        self.result_offset = 0
        self.job = None
        self.rows = {}
        self.batch_options = {}
        self.category_ids = {}
        self.pack(fill='both', expand=True)
        actions = ttk.Frame(self, padding=6)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='开始 AI 专项抽查', command=self.new_batch, style='Accent.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='停止', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=5)
        self.more_button = ttk.Button(actions, text='更多选项', command=self._toggle_options)
        self.more_button.pack(side='right')
        selection = ttk.LabelFrame(self, text='选择抽查科目（可多选；未选时默认以下六类）', padding=(6, 2))
        selection.pack(fill='x', padx=6)
        self.review_accounts = {name: tk.BooleanVar(value=False) for name in ACCOUNTS}
        for index, (name, var) in enumerate(self.review_accounts.items()):
            ttk.Checkbutton(selection, text=name, variable=var, style='Sampling.TCheckbutton').grid(row=index // 3, column=index % 3, sticky='w', padx=(0, 24))
        self.progress = tk.StringVar(value='只抽查所选科目的凭证；默认百炼优先，Jev 可选，DeepSeek Flash 兜底。')
        wrapped_label(self, textvariable=self.progress, padding=7, wraplength=1050).pack(fill='x')
        self.progress_bar = ttk.Progressbar(self, mode='determinate', maximum=1, value=0)
        self.progress_bar.pack(fill='x', padx=8, pady=(0, 7))
        self.category_frame = ttk.Frame(self, padding=(6, 0))
        self.category_frame.pack(fill='x')
        self.category = tk.StringVar(value='全部')
        self.options = ttk.Frame(self)
        batch_row = ttk.Frame(self.options, padding=(6, 0))
        batch_row.pack(fill='x')
        ttk.Label(batch_row, text='历史批次').pack(side='left', padx=(0, 5))
        ttk.Button(batch_row, text='AI审查设置', command=app.open_review_rules).pack(side='right', padx=5)
        self.batch = tk.StringVar()
        self.batch_combo = ttk.Combobox(batch_row, textvariable=self.batch, state='readonly')
        self.batch_combo.pack(fill='x', expand=True)
        self.batch_combo.bind('<<ComboboxSelected>>', self._select_batch)
        lanes = ttk.Frame(self.options, padding=(6, 0))
        lanes.pack(fill='x')
        self.lane = tk.StringVar(value='candidates')
        self.lane_buttons = {}
        for index, (label, value) in enumerate((('待核线索（含不确定／失败）', 'candidates'), ('已完成结果', 'completed'), ('命中分类', 'scored'), ('信息不足／判断不确定', 'uncertain'), ('分析失败／未分析／过期', 'unfinished'), ('全部（含未命中／未送 AI）', 'all'))):
            button = ttk.Radiobutton(lanes, text=label, value=value, variable=self.lane, command=self.refresh_rows)
            button.grid(row=index // 2, column=index % 2, sticky='w', padx=(0, 14), pady=2)
            self.lane_buttons[value] = button
        filters = ttk.Frame(self.options, padding=6)
        filters.pack(fill='x')
        self.account, self.period = (tk.StringVar(value=v) for v in ('', '全部期间'))
        self.show_ignored = tk.BooleanVar(value=False)
        ttk.Label(filters, text='科目').pack(side='left')
        entry = ttk.Entry(filters, textvariable=self.account, width=13)
        entry.pack(side='left', padx=5)
        entry.bind('<Return>', lambda _e: self.refresh_rows())
        combo = ttk.Combobox(filters, textvariable=self.period, values=['全部期间'], state='readonly', width=13)
        combo.pack(side='left', padx=5)
        combo.bind('<<ComboboxSelected>>', lambda _e: self.refresh_rows())
        self.period_combo = combo
        ttk.Checkbutton(filters, text='显示已忽略', variable=self.show_ignored, command=self.refresh_rows).pack(side='left', padx=5)
        ttk.Button(filters, text='筛选', command=self.refresh_rows).pack(side='left')
        self.view = tree(self, [('voucher', '凭证', 140), ('account', '抽查科目', 110), ('amount', '金额', 110), ('summary', '摘要', 200),
            ('categories', '审查问题 / 编号', 260),
            ('analysis', '状态', 110)])
        self.view.bind('<Double-1>', self.open_detail)
        bottom = ttk.Frame(self, padding=6)
        bottom.pack(side='bottom', fill='x', before=self.view.master)
        self.count_label = tk.StringVar()
        wrapped_label(bottom, textvariable=self.count_label).pack(side='left', fill='x', expand=True)
        ttk.Button(bottom, text='查看详情', command=self.open_detail).pack(side='right')
        self.result_prev = ttk.Button(bottom, text='上一页', command=lambda: self._result_page(-1), state='disabled')
        self.result_prev.pack(side='right', padx=3)
        self.result_next = ttk.Button(bottom, text='下一页', command=lambda: self._result_page(1), state='disabled')
        self.result_next.pack(side='right', padx=3)
        self._poll_id = self.after(200, self._poll)
        self.bind("<Destroy>", self._destroyed, add="+")

    def _render_category_bar(self, rules, counts):
        for child in self.category_frame.winfo_children():
            child.destroy()
        # 类别→ID 映射由 _category_counts 维护；这里只重建按钮，不能清空映射，
        # 否则点击分类后查不到 ID，筛选落空（表现为“点了没反应”）。
        selected = self.category.get() or '全部'
        if selected not in dict(counts) and selected != '全部':
            selected = '全部'
        for index, (label, count) in enumerate(counts):
            button = ttk.Radiobutton(self.category_frame, text=f'{label} {count}', value=label,
                variable=self.category, command=self.refresh_rows)
            button.grid(row=index // 7, column=index % 7, sticky='w', padx=(0, 10), pady=1)
        self.category.set(selected)

    def _category_counts(self, rules, candidates):
        counts = [('全部', len(candidates))]
        unclassified = 0
        self.category_ids = {'全部': ''}
        for topic in rules['topics']:
            self.category_ids[topic['label']] = topic['id']
        tallies = {topic['label']: 0 for topic in rules['topics']}
        for group in candidates:
            labels = group['labels']
            if labels == ['待分类']:
                unclassified += 1
                continue
            for label in labels:
                if label in tallies:
                    tallies[label] += 1
        counts.extend((label, tallies[label]) for label in tallies if tallies[label])
        self.category_ids['待分类'] = 'unclassified'
        if unclassified:
            counts.append(('待分类', unclassified))
        return counts

    def _destroyed(self, event):
        if event.widget is self and self._poll_id:
            self.after_cancel(self._poll_id)
            self._poll_id = None

    def _toggle_options(self):
        if self.options.winfo_manager():
            self.options.pack_forget()
            self.more_button.configure(text='更多选项')
        else:
            self.options.pack(fill='x', before=self.view.master)
            self.more_button.configure(text='收起选项')

    def new_batch(self):
        if not self.app.store:
            messagebox.showinfo('请打开账套', '请先新建或打开账套并导入明细账。', parent=self)
            return
        if self.app.import_busy or self.preparing:
            self.progress.set('请完成或取消当前本地处理后再开始审查。')
            return
        if active_job(self.app.store.path):
            messagebox.showinfo('审查正在运行', '当前账套同一时间只运行一个批次。', parent=self)
            return
        self.prepare({'review_accounts': [name for name, var in self.review_accounts.items() if var.get()]})

    def prepare(self, scope):
        if self.preparing or self.app.import_busy:
            return
        scope = {**scope, 'preferred_provider': self.app.controller.preferences.get('review_preferred_provider', 'bailian'),
                 'bailian_endpoint': self.app.controller.preferences.get('bailian_endpoint', BAILIAN_ENDPOINT)}
        if not self.app.store.large_book():
            try:
                batch_id = prepare_batch(self.app.store, scope)
            except Exception as exc:
                messagebox.showerror('无法生成预览', str(exc), parent=self)
                return
            ConsentDialog(self, batch_id)
            return
        self.preparing = True
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.set('正在本机生成完整发送预览；此阶段不会发送数据，可停止。')
        path = self.app.store.path
        def work(cancelled, report):
            store = LedgerStore.connect_existing(path, readonly=False, cancelled=cancelled)
            try:
                return prepare_batch(store, scope, cancelled=cancelled, progress=report)
            finally:
                store.close()
        def finish(batch_id):
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            ConsentDialog(self, batch_id)
        def failed(exc):
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            self.progress.set(f'预览未完成，可重试：{exc}')
        self.app.tasks.submit('review_prepare', work, finish, failed,
            lambda value: self.progress.set(f'正在本机生成预览：{value}；尚未发送数据'))

    def cancel(self):
        if self.preparing:
            self.app.tasks.cancel('review_prepare')
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            self.progress.set('已停止生成预览；未发送数据。')
        if self.app.store:
            job = active_job(self.app.store.path)
            if job:
                job.cancel.set()
                self.progress.set('已请求取消；正在发出的请求结束后保留已完成结果，停止后续发送。')

    def _select_batch(self, _event=None):
        self.batch_id = self.batch_options.get(self.batch.get())
        self.refresh()

    def refresh(self):
        store = self.app.store
        if not store or self.preparing or self.app.import_busy:
            return
        recover_interrupted(store)
        batches = store.db.execute("SELECT * FROM jev_batches WHERE status<>'preview' ORDER BY id DESC").fetchall()
        self.batch_options = {f"#{r['id']} · {r['created_at']} · {STATUS_LABELS.get(r['status'], r['status'])} · 规则 v{r['rule_id']}": r['id'] for r in batches}
        self.batch_combo.configure(values=list(self.batch_options))
        if self.batch_id not in self.batch_options.values():
            self.batch_id = batches[0]['id'] if batches else None
        self.batch.set(next((label for label, value in self.batch_options.items() if value == self.batch_id), '尚无审查批次'))
        if store.large_book() and not self._visible():
            self._rows_dirty = True
            return
        if self.batch_id is not None:
            token = (store.path, self.batch_id, store.setting('data_revision', '0'))
            if token != self._stale_token:
                self._stale_token = token
                if store.large_book():
                    # Import/removal already invalidates affected snapshots. The
                    # full fingerprint check is background-only and once per revision.
                    path, batch_id = store.path, self.batch_id
                    def work(cancelled, report):
                        reader = LedgerStore.connect_existing(path, readonly=False, cancelled=cancelled)
                        try:
                            refresh_staleness(reader, batch_id)
                        finally:
                            reader.close()
                    self.app.tasks.submit('review_stale', work, lambda _: self.refresh_rows(),
                        lambda exc: self.progress.set(f'历史结果校验未完成：{exc}'))
                else:
                    refresh_staleness(store, self.batch_id)
        self.refresh_rows()

    def _counts(self, counts=None, info=None):
        counts = counts if counts is not None else batch_counts(self.app.store, self.batch_id)
        info = info if info is not None else batch_info(self.app.store, self.batch_id)
        if not info:
            return
        running = active_job(self.app.store.path) is not None
        self.start_button.configure(state='disabled' if running or self.preparing else 'normal')
        self.cancel_button.configure(state='normal' if running else 'disabled')
        total = counts['vouchers']
        done = counts['finished_vouchers']
        self.progress_bar.configure(maximum=max(1, total), value=done)
        percent = done / total * 100 if total else 0
        status = STATUS_LABELS.get(info['status'], info['status'])
        self.progress.set(f"{status} · 已处理 {done}/{total} 张（{percent:.0f}%） · 成功 {counts['successful_vouchers']} · 失败 {counts['failed_vouchers']} · 低价值未送审 {counts['skipped_vouchers']}")
        self.lane_buttons['uncertain'].configure(text=f"信息不足／判断不确定（{counts['uncertain']}）")
        unfinished = counts['total'] - counts.get('success', 0) - counts.get('skipped', 0)
        self.lane_buttons['unfinished'].configure(text=f"失败／未分析（{unfinished}）／过期（{counts['stale']}）")

    def _result_page(self, direction):
        self.result_offset = max(0, self.result_offset + direction * 500)
        self.refresh_rows(keep_page=True)

    def refresh_rows(self, keep_page=False):
        self.app.tasks.cancel('review_rows')
        if not self.app.store or self.batch_id is None:
            self.view.delete(*self.view.get_children())
            self.rows = {}
            self.progress_bar.configure(maximum=1, value=0)
            self.count_label.set('尚无结果，选择科目后点击“开始 AI 专项抽查”。')
            self.result_prev.configure(state='disabled')
            self.result_next.configure(state='disabled')
            for child in self.category_frame.winfo_children():
                child.destroy()
            return
        if not keep_page:
            self.result_offset = 0
        self._last_refresh = time.monotonic()
        self._rows_dirty = False
        store, batch_id = self.app.store, self.batch_id
        options = (None, self.lane.get(), self.account.get().strip(),
            self.category_ids.get(self.category.get(), ''),
            '' if self.period.get() == '全部期间' else self.period.get(), self.show_ignored.get())
        cache = self._totals_cache
        def query(reader):
            revision = (reader.path, reader.setting('data_revision', '0'))
            totals = cache[1] if cache and cache[0] == revision else None
            context = voucher_result_context(reader, batch_id, totals)
            if context is None:
                return None
            groups = voucher_results(reader, batch_id, *options, context=context)
            tier, lane, account, category, period, ignored = options
            candidates = groups if lane == 'candidates' and not category else voucher_results(
                reader, batch_id, tier, 'candidates', account, '', period, ignored, context=context)
            periods = sorted({f"{r['local']['fiscal_year']}/{r['local']['period']}"
                for lines in context['groups'].values() for r in lines})
            return groups, candidates, context['rules'], periods, batch_counts(reader, batch_id), context['info'], (revision, context['totals'])
        if store.large_book():
            self.count_label.set('正在本机更新线索…')
            self.app._read_task('review_rows', query, self._render_rows)
        else:
            self._render_rows(query(store))

    def _render_rows(self, result):
        if result is None:
            return
        groups, candidates, rules, periods, counts, info, self._totals_cache = result
        self._counts(counts, info)
        if self.app.store.large_book() and not self._visible():
            self._rows_dirty = True
            return
        self._render_category_bar(rules, self._category_counts(rules, candidates))
        self.period_combo.configure(values=['全部期间', *periods])
        total = len(groups)
        self.result_offset = min(self.result_offset, max(0, (total - 1) // 500 * 500))
        groups = groups[self.result_offset:self.result_offset + 500]
        self.result_prev.configure(state='normal' if self.result_offset else 'disabled')
        self.result_next.configure(state='normal' if self.result_offset + 500 < total else 'disabled')
        old_items = set(self.view.get_children())
        self.rows = {}
        for index, group in enumerate(groups):
            item = group['key']
            self.rows[item] = group
            row = group['hits'][0]['local']
            voucher = f"{row['fiscal_year']}/{row['period']} {row['voucher_type']}{row['voucher_no']}" if row['voucher_no'] else f"无凭证号 · {row['filename']}:{row['row_no']}"
            states = list(dict.fromkeys('已过期' if r['stale'] else STATUS_LABELS[r['status']] for r in group['lines']))
            if group.get('provider'):
                states.append({'bailian': '百炼', 'jev': 'Jev', 'deepseek': 'DeepSeek Flash'}[group['provider']])
            if any(r['status'] == 'success' and is_uncertain(r) for r in group['lines']):
                states.append('需人工复核')
            if group['feedback'] == 'ignore':
                states.append('已忽略')
            if group['partial']:
                states.append('仅覆盖部分分录')
            currencies = group['sort_currency']
            amount = f"{format_money(group['sort_amount'], comma=True)} {currencies[0]}" if len(currencies) == 1 and currencies[0] != '币种未注明' else (f"{format_money(group['sort_amount'], comma=True)}" if len(currencies) == 1 else '多币种')
            values = (voucher, '、'.join(group.get('review_accounts', [])) or '历史通用审查', amount, '；'.join(dict.fromkeys(r['local']['summary'] for r in group['hits'])),
                '、'.join(f'{identifier} {label}' for identifier, label, _sources, _probability in group['categories']) or '待分类', '、'.join(states))
            if item in old_items:
                self.view.item(item, values=values)
                old_items.remove(item)
            else:
                self.view.insert('', 'end', iid=item, values=values)
            self.view.move(item, '', index)
        self.view.delete(*old_items)
        self.count_label.set(f'共 {total:,} 张 · 第 {self.result_offset // 500 + 1}/{max(1, (total + 499) // 500)} 页 · 双击查看完整凭证及提示词命中事项。')

    def open_detail(self, _event=None):
        selected = self.view.selection()
        if selected:
            ReviewDetail(self, self.rows[selected[0]])

    def _visible(self):
        return self.app.current_page() == 'review'

    def _poll(self):
        if self.app.store:
            job = active_job(self.app.store.path) or self.job
            if job:
                changed = False
                done = False
                try:
                    while True:
                        kind, value = job.events.get_nowait()
                        changed = True
                        done |= kind == 'done'
                        if kind == 'error':
                            messagebox.showerror('审查中断', value, parent=self)
                except Empty:
                    pass
                self._rows_dirty |= changed
                if done:
                    self.job = None
                    if self._visible():
                        self.refresh()
                    else:
                        self.progress.set('本批次已结束；打开 AI 尽调线索可查看最新结果。')
                elif self._rows_dirty and self.batch_id == job.batch_id and time.monotonic() - self._last_refresh >= 1:
                    if self._visible():
                        if not self.app.tasks.busy('review_rows'):
                            self.refresh_rows(keep_page=True)
                    else:
                        self.progress.set('正在后台审查；返回本页后更新线索。')
        self._poll_id = self.after(250, self._poll)


class ReviewDetail(tk.Toplevel):
    """AI 尽调线索详情：只保留分类、完整凭证、按需查看的 JSON 返回与关注操作。"""

    def __init__(self, panel, group):
        super().__init__(panel)
        from .ui import VIEW_COLUMNS, amount, tree
        from .voucher_notes import list_marks, mark_for_entry, remove_mark, save_mark, voucher_key
        self._mark_tools = (mark_for_entry, remove_mark, save_mark, list_marks)
        self.panel, self.app, self.group = panel, panel.app, group
        self.app.review_windows.add(self)
        self.bind('<Destroy>', lambda event: self.app.review_windows.discard(self) if event.widget is self else None, add='+')
        self.store = self.app.store
        local = group['hits'][0]['local']
        voucher_title = f"{local['fiscal_year']}年{local['period']}期 {local['voucher_type']}{local['voucher_no']}" if local['voucher_no'] else '无凭证号来源行'
        self.title(f'AI 尽调线索 · {voucher_title}')
        self.geometry('840x560')
        self.minsize(640, 420)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        info = batch_info(self.store, panel.batch_id)
        self.rules = json.loads(info['body_json'])
        models = list(dict.fromkeys(json.loads(row['response_json'])['model'] for row in group['lines'] if row['response_json']))
        wrapped_label(self, text=f"命中事项：{'、'.join(group['labels'])} · "
            f"规则 v{info['rule_id']} · 实际模型 {'、'.join(models) or '尚未调用'}\n{BOUNDARY}", padding=10, wraplength=760).grid(row=0, column=0, sticky='ew')
        self.detail_panes = ttk.Panedwindow(self, orient='vertical')
        self.detail_panes.grid(row=1, column=0, sticky='nsew', padx=10)
        self.voucher_area = ttk.Frame(self.detail_panes)
        self.detail_panes.add(self.voucher_area, weight=0)
        self.preview_tabs = ttk.Notebook(self.voucher_area)
        self.preview_tabs.pack(fill='both', expand=True)
        self.voucher_page = ttk.Frame(self.preview_tabs)
        self.preview_tabs.add(self.voucher_page, text='完整凭证')
        topics = {topic['id']: topic for topic in self.rules['topics']}
        suggestions = []
        for identifier, label, _sources, _probability in group['categories']:
            topic = topics[identifier]
            suggestions.append(f"{topic.get('account', '')} · {identifier} {label}\n建议核查：{topic['advice']}"
                + (f"\n待归入法律尽调问题；建议分类：{topic['category']}，也可选择律师自定义问题。" if topic.get('category') else ''))
        if suggestions:
            advice_page = ttk.Frame(self.preview_tabs)
            self.preview_tabs.add(advice_page, text='核查建议')
            read_only_text(advice_page, '\n\n'.join(suggestions), height=4)
        # 完整凭证：与明细账双击打开的凭证同一份数据与列格式。
        self.entry_id = group['lines'][0]['entry_id']
        lines = self.store.voucher_lines(self.entry_id)
        self.voucher_view = tree(self.voucher_page, VIEW_COLUMNS['voucher'])
        self._voucher_rows = min(6, max(2, len(lines)))
        self.voucher_view.configure(height=self._voucher_rows)
        self.app._register_column_view('voucher', self.voucher_view)
        mark_for_entry, _remove_mark, _save_mark, _list_marks = self._mark_tools
        existing_mark = mark_for_entry(self.store, self.entry_id)
        for line in lines:
            side_tag = 'debit_row' if line['debit_cents'] and not line['credit_cents'] else 'credit_row' if line['credit_cents'] and not line['debit_cents'] else ''
            self.voucher_view.insert('', 'end', values=(line['code'], line['name'], line['summary'], line['auxiliary'],
                line['currency'], line['original_amount'], amount(line['debit_cents']), amount(line['credit_cents']),
                f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'),
                tags=('marked',) if existing_mark else ((side_tag,) if side_tag else ()))
        debit = sum(line['debit_cents'] for line in lines)
        credit = sum(line['credit_cents'] for line in lines)
        ttk.Label(self.voucher_page, text=f"{len(lines)} 行分录   借方 {amount(debit) or '0.00'}   贷方 {amount(credit) or '0.00'}   差额 {amount(debit - credit) or '0.00'}",
            padding=(2, 5)).pack(side='bottom', anchor='w', before=self.voucher_view.master)
        # A separate grid row reserves the action bar before distributing space
        # between the voucher and the note editor, including at minimum size.
        self.actions = ttk.Frame(self, padding=(10, 4))
        self.actions.grid(row=2, column=0, sticky='ew')
        self.actions.columnconfigure(2, weight=1)
        self.json_button = ttk.Button(self.actions, text='查看 JSON 返回结果 ▾', command=self._toggle_json)
        self.json_button.grid(row=0, column=0, columnspan=2, sticky='w', pady=3)
        self.ignore_var = tk.BooleanVar(value=group['feedback'] == 'ignore')
        ttk.Checkbutton(self.actions, text='忽略此线索', variable=self.ignore_var, command=self._save_ignore).grid(row=0, column=2, sticky='w', padx=8)
        self.close_button = ttk.Button(self.actions, text='关闭', command=self.request_close)
        self.close_button.grid(row=1, column=4, sticky='e', padx=(6, 0), pady=3)
        ttk.Button(self.actions, text='加入尽调问题…', command=lambda: self.app.workbench.choose_issue([group['key']])).grid(row=0, column=3, columnspan=2, sticky='e')
        collapsed = '查看 / 编辑关注备注' if existing_mark else '关注这张凭证'
        self.note_toggle = ttk.Button(self.actions, text=collapsed, command=self._toggle_notes, style='Accent.TButton')
        self.note_toggle.grid(row=1, column=0, columnspan=2, sticky='w', pady=3)
        self.note_area = ttk.Frame(self.detail_panes, padding=(2, 8, 2, 0))
        self.attention = tk.BooleanVar(value=bool(existing_mark))
        ttk.Checkbutton(self.note_area, text='关注这张凭证', variable=self.attention).pack(anchor='w')
        self.note = ScrolledText(self.note_area, height=6, wrap='word', undo=True)
        self.note.pack(fill='both', expand=True)
        if existing_mark:
            self.note.insert('1.0', existing_mark['note'])
        self._saved_note = (self.attention.get(), self.note.get('1.0', 'end-1c'))
        self.feedback = tk.StringVar()
        self.note_key = voucher_key(lines[0]) if lines else group['key']
        self.note_draft = self.app.note_hub.bind(self, self.note, self.note_key, self.feedback, on_saved=self._note_saved)
        ttk.Label(self.note_area, textvariable=self.feedback, foreground='#555').pack(side='bottom', anchor='w', before=self.note.frame)
        self.note_save_button = ttk.Button(self.actions, text='保存关注与备注', command=self._save_attention, style='Accent.TButton')
        self.note_save_button.grid(row=1, column=3, sticky='e', pady=3)
        self.note_reload_button = ttk.Button(self.actions, text='重读备注…', command=lambda: self.app.note_hub.reload(self.note))
        self.note_reload_button.grid(row=1, column=2, sticky='w', padx=8)
        self.note_save_button.grid_remove()
        self.note_reload_button.grid_remove()
        self.json_area = ttk.Frame(self.preview_tabs)
        self.json_text = None
        self.bind('<Escape>', lambda _event: self.request_close())
        self.protocol('WM_DELETE_WINDOW', self.request_close)

    def request_close(self):
        if not self.note_draft.flush():
            self.note.focus_set()
            return False
        if self.attention.get() != self._saved_note[0] and not self._save_attention():
            return False
        self.destroy()
        return True

    def _note_saved(self):
        from .issue_store import note_record
        current = note_record(self.store, self.note_key)
        pending_attention = self.attention.get() != self._saved_note[0]
        if not pending_attention:
            self.attention.set(bool(current))
        self._saved_note = (bool(current), self.note.get('1.0', 'end-1c'))
        for item in self.voucher_view.get_children():
            self.voucher_view.item(item, tags=('marked',) if current else ())
        self.app.update_mark_indicators(self.note_key)

    def _toggle_json(self):
        if str(self.json_area) in self.preview_tabs.tabs():
            self.preview_tabs.forget(self.json_area)
            self.json_button.configure(text='查看 JSON 返回结果 ▾')
            return
        if self.json_text is None:
            self.json_text = read_only_text(self.json_area, self._json_content(), height=14)
        self.preview_tabs.add(self.json_area, text='模型返回')
        self.preview_tabs.select(self.json_area)
        self.json_button.configure(text='收起 JSON 返回结果 ▴')

    def _json_content(self) -> str:
        """取本凭证第一份成功返回；同凭证各分录共用同一次请求的结果。"""
        for row in self.group['lines']:
            if row['response_json']:
                data = json.loads(row['response_json'])
                if data.get('provider') == 'deepseek':
                    parts = [f"实际模型：{data['model']} · DeepSeek 明确命中 · 信息不足：{data['insufficient']}"]
                    if data.get('fallback_reason'):
                        parts.append('切换原因：' + data['fallback_reason'])
                    topics = {topic['id']: topic for topic in self.rules['topics']}
                    for hit in data['hits']:
                        topic = topics[hit['id']]
                        parts.append(f"{topic.get('account', '')} · {hit['id']} {topic['label']}\n原文依据：{hit['evidence']}\n建议核查：{topic['advice']}")
                    parts += ['', '完整结构化返回：', json.dumps(data, ensure_ascii=False, indent=2)]
                    return '\n'.join(parts)
                answers = data['answers']
                provider_label = '百炼' if data.get('provider') == 'bailian' else 'Jev'
                parts = [f"实际模型：{data['model']} · {provider_label} · "
                         f"信息不足：{'是' if is_uncertain(row) else '否'} · 分段数：{data.get('chunks', 1)} · "
                         f"{'缓存复用，未再次请求' if row['cached'] else '本次请求'}"]
                accepted = {item[0] for item in self.group['categories']}
                for topic in self.rules['topics']:
                    answer = answers.get(topic['id'])
                    if answer is None:
                        continue
                    hit = answer.get('choice') == 'hit' if answer.get('type') == 'choice' else topic['id'] in accepted
                    parts.append(f"{topic['id']} {topic['label']}：{'命中' if hit else '不命中'}"
                        + (f"\n建议核查：{topic['advice']}" if hit else ''))
                if data.get('chunks', 1) > 1:
                    parts.append('分段结果：合并各片段明确命中的事项，需人工结合全文复核。')
                parts.append(f"同次请求用量（{data.get('shared_entries', 1)} 条分录共用，不逐行累加）：{data['usage']}")
                parts += ['', '完整结构化回答：', json.dumps(answers, ensure_ascii=False, indent=2)]
                return '\n'.join(parts)
            if row['error']:
                return row['error']
        return '（本凭证未送 AI，或尚无返回结果）'

    def _toggle_notes(self):
        if str(self.note_area) in self.detail_panes.panes():
            if not self.note_draft.flush():
                self.note.focus_set()
                return
            self.detail_panes.forget(self.note_area)
            self.note_save_button.grid_remove()
            self.note_reload_button.grid_remove()
            mark_for_entry, _remove, _save, _list = self._mark_tools
            self.note_toggle.configure(text='查看 / 编辑关注备注' if mark_for_entry(self.store, self.entry_id) else '关注这张凭证')
        else:
            self.attention.set(True)
            self.detail_panes.add(self.note_area, weight=1)
            self.note_toggle.configure(text='收起备注')
            self.note_save_button.grid()
            self.note_reload_button.grid()
            self.after_idle(self._size_note_panes)
            self.note.focus_set()

    def _size_note_panes(self):
        if not self.winfo_exists() or str(self.note_area) not in self.detail_panes.panes():
            return
        row_height = int(ttk.Style(self).lookup('Treeview', 'rowheight') or 24)
        preferred = max(self.voucher_area.winfo_reqheight(), self._voucher_rows * row_height + 84)
        available = self.detail_panes.winfo_height()
        self.detail_panes.sashpos(0, min(preferred, max(60, int(available * 0.42))))

    def _save_attention(self):
        from .issue_store import write_note
        mark_for_entry, remove_mark, _save_mark, _list_marks = self._mark_tools
        requested = self.attention.get()
        if not self.note_draft.flush() or not self.app.note_hub.flush():
            return False
        try:
            current = mark_for_entry(self.store, self.entry_id)
            if requested and current is None:
                write_note(self.store, self.note_key, None, self.note.get('1.0', 'end-1c'))
            elif not requested and current:
                remove_mark(self.store, self.note_key)
            self.app.note_hub.broadcast(self.note_key)
            self._note_saved()
            self.feedback.set('已保存至关注凭证' if requested else '已取消关注；问题关联保留')
        except Exception as exc:
            self.feedback.set(f'保存失败：{exc}')
            return False
        self.app.refresh_marks()
        return True

    def _save_ignore(self):
        save_feedback(self.store, self.group['key'], 'ignore' if self.ignore_var.get() else '')
        self.panel.refresh_rows()
