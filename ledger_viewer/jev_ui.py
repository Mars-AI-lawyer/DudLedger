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
from .window_position import center_window
from .store import LedgerStore
from .importer import format_money
from .jev_mapping import get_api_key
from .deepseek_mapping import get_api_key as get_deepseek_key
from .bailian_review import get_api_key as get_bailian_key, DEFAULT_ENDPOINT as BAILIAN_ENDPOINT
from .specialist_rules import ACCOUNTS, load_rules, review_accounts
from .review_rules_ui import ReviewRulesDialog
from .related_parties import party_names, review_party_context
from .review_providers import order_description, preferred_provider
from .jev_review import (BOUNDARY, STATUS_LABELS, SEND_FIELDS,
    ReviewJob, active_job, batch_counts, batch_info, discard_preview, digest,
    is_uncertain, prepare_batch, preview_page, recover_interrupted,
    refresh_staleness, save_feedback, test_connection, voucher_results, voucher_result_context)

CONSENT_TEXT = ('本次将把所选账簿记录中的日期、科目、摘要、往来单位、辅助核算、非零一侧的借贷金额，'
    '以及当前账套尽调主体名单中的名称、别名、身份及与目标公司关系（不含备注），按本次优先顺序使用阿里百炼决策模型、TypeSafe Jev、DeepSeek Flash（不思考）；前一级未配置、额度不足或服务不可用时尝试下一级，'
    '用于筛选值得法律尽调律师进一步查看的凭证。百炼仅在使用者确认免费可用后启用；未确认时跳过。\n\n'
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


def review_toolbar(bar, leading, trailing, *, text_width=None):
    bar.columnconfigure(0, weight=1)
    leading.grid(row=0, column=0, sticky='ew')
    trailing.grid(row=0, column=1, sticky='e')

    def reflow(event=None):
        width = event.width if event is not None else bar.winfo_width()
        required = (text_width or leading.winfo_reqwidth()) + trailing.winfo_reqwidth() + 16
        if width < required:
            trailing.grid_configure(row=1, column=0, columnspan=2, pady=(3, 0))
        else:
            trailing.grid_configure(row=0, column=1, columnspan=1, pady=0)

    bar.bind('<Configure>', reflow)
    bar.after_idle(reflow)


def account_choice_positions(width, option_widths):
    positions = []
    offset = row = 0
    for option_width in option_widths:
        fitted = min(width, option_width)
        if offset and offset + fitted > width:
            offset = 0
            row += 1
        positions.append((offset, row, fitted))
        offset += fitted + 8
    return positions, row + 1 if positions else 0


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


def saved_voucher_lines(group):
    """Return the voucher as saved with the batch, without consulting current entries."""
    lines = []
    for row in group.get('lines', []):
        local = row.get('local')
        if local is None and row.get('local_json'):
            local = json.loads(row['local_json'])
        if local is not None:
            lines.append(local)
    return lines


def detail_lines(group, current_lines):
    """Use current voucher rows only while every saved result still matches them."""
    historical = not current_lines or any(row.get('stale') for row in group.get('lines', []))
    return (saved_voucher_lines(group) if historical else current_lines), historical


def sent_snapshot_content(group):
    blocks = []
    for index, row in enumerate(group.get('lines', []), 1):
        payload = json.loads(row['input_json']) if isinstance(row.get('input_json'), str) else row.get('input_json', {})
        blocks.append(f'第 {index} 条已保存发送内容\n{display_input(payload)}')
    return '\n\n'.join(blocks) or '（本批次未保存可显示的发送内容）'


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
            if field == 'review_accounts':
                description.append('专项科目：' + ('、'.join(value) if value else '全部已设置科目（未选择 = 全部）'))
            elif value and field in labels:
                description.append(f'{labels[field]}：' + ('、'.join(value) if isinstance(value, list) else str(value)))
        wrapped_label(self, text='本次将选定凭证及其完整分录、尽调主体名单和程序特征发送至下列模型服务；可能包含客户商业信息或个人信息，请确认符合项目保密安排。原始文件不上传。完整说明、名单及实际发送字段见下方预览。', wraplength=750, padding=12).pack(fill='x')
        order = order_description(scope.get('preferred_provider'), bailian_enabled=scope.get('bailian_free_confirmed') is True)
        wrapped_label(self, text='本次优先顺序：' + order, padding=(12, 0)).pack(fill='x')
        scope_note = '；按专项科目新建范围，不继承当前明细筛选' if 'review_accounts' in scope else ''
        wrapped_label(self, text=f'账套：{self.store.path.name}\n范围：' + ('；'.join(description) or '全部明细分录') + scope_note + f'\n共 {self.total} 条分录，归为 {self.voucher_total} 张凭证／无号来源行；每张凭证去重合并后核查（相同行合并金额并标注次数，仅发送非零一侧金额）；超长内容分段 · 规则 v{info["rule_id"]} · {info["model"]}', padding=(12, 4), wraplength=750).pack(fill='x')
        parties = party_names(self.store.related_parties())
        grouped = review_party_context(self.store.related_parties())
        self.party_payload = grouped
        wrapped_label(self, text='发送字段：' + '、'.join(SEND_FIELDS.values())
            + f'；尽调主体名单共 {len(parties)} 个名称（名称、别名、身份及与目标公司关系，不含备注）。文件名、路径、制单人及凭证标识留在本地。', padding=(12, 4), wraplength=750).pack(fill='x')
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
            self.panel._start_prepared_batch(self.store, self.batch_id)
        except Exception as exc:
            messagebox.showerror('未开始审查', str(exc), parent=self)
            return
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
        self._result_scope = None
        self.job = None
        self.rows = {}
        self.batch_options = {}
        self.category_ids = {}
        self.pack(fill='both', expand=True)
        actions = ttk.Frame(self, padding=6)
        actions.pack(fill='x')
        run_actions = ttk.Frame(actions)
        settings_actions = ttk.Frame(actions)
        self.start_button = ttk.Button(run_actions, text='开始 AI 专项抽查', command=self.new_batch, style='Accent.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(run_actions, text='停止', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=5)
        self.more_button = ttk.Button(settings_actions, text='更多选项 ▾', style="Ghost.TButton", command=self._toggle_options)
        self.more_button.pack(side='right')
        review_toolbar(actions, run_actions, settings_actions)
        wrapped_label(self, text='点击开始会向已配置的 AI 服务发送所选凭证分录、尽调主体信息及必要的历史背景；不上传原文件。',
                      style='Hint.TLabel', padding=(8, 2), wraplength=1050).pack(fill='x')
        self.overview = tk.StringVar(value='待核 0 张 · 当前批次：尚无审查批次')
        wrapped_label(self, textvariable=self.overview, padding=(8, 2), wraplength=1050).pack(fill='x')
        self.scope_summary = tk.StringVar()
        self.options = ttk.Frame(self)
        self.scope_options = ttk.LabelFrame(self, padding=(6, 2))
        scope_label = ttk.Label(self.scope_options, textvariable=self.scope_summary)
        self.scope_options.configure(labelwidget=scope_label)
        self.scope_options.pack(fill='x', padx=6)
        self.account_host = ttk.Frame(self.scope_options)
        self.account_host.pack(fill='x')
        self.account_host.columnconfigure(0, weight=1)
        self.account_canvas = tk.Canvas(self.account_host, width=1, height=1, highlightthickness=0)
        self.account_canvas.grid(row=0, column=0, sticky='ew')
        self.account_scroll = ttk.Scrollbar(self.account_host, orient='vertical', command=self.account_canvas.yview)
        self.account_canvas.configure(yscrollcommand=self.account_scroll.set)
        self.account_selection = ttk.Frame(self.account_canvas)
        self.account_window = self.account_canvas.create_window(0, 0, window=self.account_selection, anchor='nw')
        self.account_host.bind('<Configure>', self._layout_account_choices)
        self.account_canvas.bind('<MouseWheel>', self._scroll_accounts)
        self.account_selection.bind('<MouseWheel>', self._scroll_accounts)
        self.review_accounts = {}
        self.refresh_accounts()
        self.progress = tk.StringVar(value='尚未开始抽查。')
        self.progress_label = wrapped_label(self, textvariable=self.progress, padding=7, wraplength=1050)
        self.progress_label.pack(fill='x')
        self.progress_bar = ttk.Progressbar(self, mode='determinate', maximum=1, value=0)
        self.progress_bar.pack(fill='x', padx=8, pady=(0, 7))
        primary_lanes = ttk.Frame(self, padding=(6, 0, 6, 4))
        primary_lanes.pack(fill='x')
        self.lane = tk.StringVar(value='candidates')
        self.lane_buttons = {}
        for label, value in (('待核', 'candidates'), ('已处理', 'completed'), ('全部', 'all')):
            button = ttk.Radiobutton(primary_lanes, text=label, value=value, variable=self.lane, command=self.refresh_rows)
            button.pack(side='left', padx=(0, 18))
            self.lane_buttons[value] = button
        batch_row = ttk.Frame(self.options, padding=(6, 0))
        batch_row.pack(fill='x')
        ttk.Label(batch_row, text='审查批次').pack(side='left', padx=(0, 5))
        self.batch = tk.StringVar()
        self.batch_combo = ttk.Combobox(batch_row, textvariable=self.batch, state='readonly', width=20)
        self.batch_combo.pack(fill='x', expand=True)
        self.batch_combo.bind('<<ComboboxSelected>>', self._select_batch)
        filters = ttk.Frame(self.options, padding=6)
        filters.pack(fill='x')
        self.show_ignored = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text='显示已忽略', variable=self.show_ignored, command=self.refresh_rows).pack(side='left', padx=5)
        self.category = tk.StringVar(value='全部')
        self.category_choices = ['全部']
        ttk.Label(filters, text='问题分类').pack(side='left', padx=(10, 5))
        self.category_combo = ttk.Combobox(filters, values=('全部',), state='readonly', width=12)
        self.category_combo.current(0)
        self.category_combo.pack(side='left', fill='x', expand=True)
        self.category_combo.bind('<<ComboboxSelected>>', self._select_category)
        self.view = tree(self, [('voucher', '凭证', 140), ('account', '抽查科目', 110), ('amount', '金额', 110), ('summary', '摘要', 200),
            ('categories', '审查问题 / 编号', 260),
            ('analysis', '状态', 110)])
        self.view.configure(selectmode='extended')
        self.view.bind('<Double-1>', self.open_detail)
        self.view.bind('<Return>', self.open_detail)
        self.view.bind('<<TreeviewSelect>>', self._update_bulk_actions)
        for sequence in ('<Command-a>', '<Control-a>'):
            self.view.bind(sequence, self._select_page)
        bulk = ttk.Frame(self, padding=(6, 0, 6, 4))
        bulk.pack(fill='x', before=self.view.master)
        bulk_actions = ttk.Frame(bulk)
        self.select_page_button = ttk.Button(bulk_actions, text='全选本页', command=self._select_page)
        self.select_page_button.pack(side='left')
        self.mark_button = ttk.Button(bulk_actions, text='关注', command=self._mark_selected, state='disabled')
        self.mark_button.pack(side='left', padx=5)
        self.join_button = ttk.Button(bulk_actions, text='加入问题工作台…', command=self._join_selected, state='disabled')
        self.join_button.pack(side='left')
        self.selection_label = tk.StringVar()
        selection = ttk.Label(bulk, textvariable=self.selection_label, style='Hint.TLabel')
        review_toolbar(bulk, bulk_actions, selection)
        self._update_bulk_actions()
        bottom = ttk.Frame(self, padding=6)
        bottom.pack(side='bottom', fill='x', before=self.view.master)
        self.count_label = tk.StringVar()
        result_info = wrapped_label(bottom, textvariable=self.count_label, wraplength=220)
        paging = ttk.Frame(bottom)
        self.result_prev = ttk.Button(paging, text='上一页', command=lambda: self._result_page(-1), state='disabled')
        self.result_prev.pack(side='left')
        self.result_next = ttk.Button(paging, text='下一页', command=lambda: self._result_page(1), state='disabled')
        self.result_next.pack(side='left', padx=3)
        ttk.Button(paging, text='查看详情', command=self.open_detail).pack(side='left')
        review_toolbar(bottom, result_info, paging, text_width=210)
        from .empty_state import EmptyState
        self.empty_initial = EmptyState(self.view.master, 'entries', '还没有 AI 审查结果',
            '请在上方选择抽查科目并开始。')
        self.empty_results = EmptyState(self.view.master, 'filter', '当前视图没有结果',
            '可切换到全部结果，看看本批次已分析的其他凭证。', '查看全部结果', self._show_all_results)
        self._poll_id = self.after(200, self._poll)
        self.bind("<Destroy>", self._destroyed, add="+")

    def refresh_accounts(self):
        names = review_accounts(load_rules(self.app.store)[1]) if self.app.store else ACCOUNTS
        if tuple(self.review_accounts) == tuple(names):
            self._update_scope_summary()
            return
        selected = {name for name, var in self.review_accounts.items() if var.get()}
        for child in self.account_selection.winfo_children():
            child.destroy()
        self.review_accounts = {name: tk.BooleanVar(value=name in selected) for name in names}
        for name, var in self.review_accounts.items():
            option = ttk.Checkbutton(self.account_selection, text=name, variable=var, style='Sampling.TCheckbutton',
                                     command=self._update_scope_summary)
            option.bind('<MouseWheel>', self._scroll_accounts)
        self.after_idle(self._layout_account_choices)
        self._update_scope_summary()

    def _layout_account_choices(self, event=None):
        width = event.width if event is not None else self.account_host.winfo_width()
        if width <= 1:
            return
        options = self.account_selection.winfo_children()
        widths = [option.winfo_reqwidth() for option in options]
        row_height = max((option.winfo_reqheight() for option in options), default=1)
        positions, rows = account_choice_positions(width, widths)
        if rows > 3:
            self.account_scroll.grid(row=0, column=1, sticky='ns')
            width = max(1, width - self.account_scroll.winfo_reqwidth())
            positions, rows = account_choice_positions(width, widths)
        else:
            self.account_scroll.grid_remove()
        for option, (left, row, option_width) in zip(options, positions):
            option.place(x=left, y=row * row_height, width=option_width, height=row_height)
        height = max(1, rows * row_height)
        self.account_canvas.itemconfigure(self.account_window, width=width, height=height)
        self.account_canvas.configure(height=min(height, 3 * row_height), scrollregion=(0, 0, width, height))
        if rows <= 3:
            self.account_canvas.yview_moveto(0)

    def _scroll_accounts(self, event):
        if event.delta:
            self.account_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _update_scope_summary(self):
        selected = [name for name, var in self.review_accounts.items() if var.get()]
        scope = f'已选 {len(selected)} 个' if selected else f'未勾选即全部（{len(self.review_accounts)} 个）'
        self.scope_summary.set(f'抽查科目 · {scope}')

    def _render_categories(self, counts):
        selected = self.category.get()
        if selected not in dict(counts) and selected in self.category_ids and selected != '全部':
            counts = [*counts, (selected, 0)]
        self.category_choices = [label for label, _count in counts]
        self.category_combo.configure(values=[f'{label}（{count}）' for label, count in counts])
        index = self.category_choices.index(selected) if selected in self.category_choices else 0
        self.category_combo.current(index)
        self.category.set(self.category_choices[index])

    def _select_category(self, _event=None):
        index = self.category_combo.current()
        if index >= 0:
            self.category.set(self.category_choices[index])
            self.refresh_rows()

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
            self.more_button.configure(text='更多选项 ▾')
        else:
            self.options.pack(fill='x', padx=6, before=self.progress_label)
            self.more_button.configure(text='更多选项 ▴')

    def _show_all_results(self):
        self.lane.set('all')
        self.category.set('全部')
        self.show_ignored.set(False)
        self.refresh_rows()

    def new_batch(self, *, preview=False):
        if not self.app.store:
            self.progress.set('请先新建或打开账套并导入明细账。')
            return
        if self.app.import_busy or self.preparing:
            self.progress.set('请完成或取消当前本地处理后再开始审查。')
            return
        if active_job(self.app.store.path):
            self.progress.set('当前账套已有正在运行的抽查。')
            return
        scope = {'review_accounts': [name for name, var in self.review_accounts.items() if var.get()]}
        if preview:
            self.prepare(scope, preview=True)
        else:
            self.prepare(scope)

    def prepare(self, scope, *, preview=False):
        if self.preparing or self.app.import_busy:
            return
        store = self.app.store
        scope = {**scope, 'preferred_provider': preferred_provider(self.app.controller.preferences.get('review_preferred_provider')),
                 'bailian_free_confirmed': self.app.controller.preferences.get('bailian_free_confirmed') is True,
                 'bailian_endpoint': self.app.controller.preferences.get('bailian_endpoint', BAILIAN_ENDPOINT)}
        def finish(batch_id):
            if self.app.store is not store:
                return
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            if preview:
                ConsentDialog(self, batch_id)
                return
            try:
                self._start_prepared_batch(store, batch_id)
            except Exception as exc:
                self.progress.set(f'未开始抽查：{exc}')
                discard_preview(store, batch_id)

        def failed(exc):
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            self.progress.set(f'凭证整理未完成，可重试：{exc}')

        if not store.large_book():
            try:
                batch_id = prepare_batch(store, scope)
            except Exception as exc:
                failed(exc)
                return
            finish(batch_id)
            return
        self.preparing = True
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.set('正在本机整理抽查凭证；尚未发送数据，可停止。')
        path = store.path
        def work(cancelled, report):
            worker_store = LedgerStore.connect_existing(path, readonly=False, cancelled=cancelled)
            try:
                return prepare_batch(worker_store, scope, cancelled=cancelled, progress=report)
            finally:
                worker_store.close()
        self.app.tasks.submit('review_prepare', work, finish, failed,
            lambda value: self.progress.set(f'正在本机整理凭证：{value}；尚未发送数据'))

    def _start_prepared_batch(self, store, batch_id):
        job = ReviewJob(store.path, batch_id, get_api_key(), deepseek_key=get_deepseek_key(), bailian_key=get_bailian_key())
        job.start(store)
        self.batch_id = batch_id
        self.job = job
        self.lane.set('candidates')
        self.category.set('全部')
        self.show_ignored.set(False)
        self.view.selection_remove(*self.view.selection())
        self.start_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.refresh()

    def cancel(self):
        if self.preparing:
            self.app.tasks.cancel('review_prepare')
            self.preparing = False
            self.start_button.configure(state='normal')
            self.cancel_button.configure(state='disabled')
            self.progress.set('已停止整理本次抽查凭证；未发送数据。')
        if self.app.store:
            job = active_job(self.app.store.path)
            if job:
                job.cancel.set()
                self.progress.set('已请求取消；正在发出的请求结束后保留已完成结果，停止后续发送。')

    def _select_batch(self, _event=None):
        self.batch_id = self.batch_options.get(self.batch.get())
        self.category.set('全部')
        self.refresh()

    def refresh(self):
        store = self.app.store
        if not store or self.preparing or self.app.import_busy:
            return
        self.refresh_accounts()
        recover_interrupted(store)
        batches = store.db.execute("SELECT * FROM jev_batches WHERE status<>'preview' ORDER BY id DESC").fetchall()
        self.batch_options = {f"#{r['id']} · {r['created_at']} · {STATUS_LABELS.get(r['status'], r['status'])} · 规则 v{r['rule_id']}": r['id'] for r in batches}
        self.batch_combo.configure(values=list(self.batch_options))
        if self.batch_id not in self.batch_options.values():
            self.batch_id = batches[0]['id'] if batches else None
        self.batch.set(next((label for label, value in self.batch_options.items() if value == self.batch_id), '尚无审查批次'))
        self.overview.set(('待核计算中 · ' if self.batch_id is not None else '待核 0 张 · ') + '当前批次：' + self.batch.get())
        if store.large_book() and not self._visible():
            self._rows_dirty = True
            return
        if self.batch_id is not None:
            token = (store.path, self.batch_id, store.setting('data_revision', '0'),
                     digest(review_party_context(store.related_parties())))
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
        if running != getattr(self, "_app_busy", False):
            self._app_busy = running
            if running:
                self.app.busy_start("AI 抽查进行中…")
            else:
                self.app.busy_stop()
        self.start_button.configure(state='disabled' if running or self.preparing else 'normal')
        self.cancel_button.configure(state='normal' if running else 'disabled')
        total = counts['vouchers']
        done = counts['finished_vouchers']
        self.progress_bar.configure(maximum=max(1, total), value=done)
        percent = done / total * 100 if total else 0
        status = STATUS_LABELS.get(info['status'], info['status'])
        self.progress.set(f"{status} · 已处理 {done}/{total} 张（{percent:.0f}%） · 成功 {counts['successful_vouchers']} · 失败 {counts['failed_vouchers']} · 低价值未送审 {counts['skipped_vouchers']}")

    def _result_page(self, direction):
        self.result_offset = max(0, self.result_offset + direction * 500)
        self.refresh_rows(keep_page=True)

    def refresh_rows(self, keep_page=False):
        self.app.tasks.cancel('review_rows')
        if not self.app.store or self.batch_id is None:
            self.view.delete(*self.view.get_children())
            self.rows = {}
            self._result_scope = None
            self._update_bulk_actions()
            self.progress_bar.configure(maximum=1, value=0)
            self.overview.set('待核 0 张 · 当前批次：尚无审查批次')
            self.count_label.set('尚无结果，选择科目后点击“开始 AI 专项抽查”。')
            self.empty_results.hide()
            self.empty_initial.show()
            self.result_prev.configure(state='disabled')
            self.result_next.configure(state='disabled')
            self.category_ids = {}
            self.category.set('全部')
            self._render_categories([('全部', 0)])
            return
        self.empty_initial.hide()
        if not keep_page:
            self.result_offset = 0
        self._last_refresh = time.monotonic()
        self._rows_dirty = False
        store, batch_id = self.app.store, self.batch_id
        lane, category, ignored = self.lane.get(), self.category_ids.get(self.category.get(), ''), self.show_ignored.get()
        scope = (store.path, batch_id, lane, category, ignored, self.result_offset)
        if scope != self._result_scope:
            self._result_scope = None
            self.view.selection_remove(*self.view.selection())
            self._update_bulk_actions()
        cache = self._totals_cache
        def query(reader):
            revision = (reader.path, reader.setting('data_revision', '0'))
            totals = cache[1] if cache and cache[0] == revision else None
            context = voucher_result_context(reader, batch_id, totals)
            if context is None:
                return None
            unfiltered = voucher_results(reader, batch_id, lane=lane, include_ignored=ignored, context=context)
            if not ignored and lane != 'candidates':
                unfiltered = [group for group in unfiltered if group['feedback'] != 'ignore']
            if category == 'unclassified':
                groups = [group for group in unfiltered if not group['categories']]
            elif category:
                groups = [group for group in unfiltered if any(item[0] == category for item in group['categories'])]
            else:
                groups = unfiltered
            pending = unfiltered if lane == 'candidates' and not ignored else voucher_results(
                reader, batch_id, lane='candidates', context=context)
            return groups, unfiltered, len(pending), context['rules'], batch_counts(reader, batch_id), context['info'], (revision, context['totals'])
        if store.large_book():
            self.count_label.set('正在本机更新线索…')
            self.app._read_task('review_rows', query, lambda result: self._render_rows(result, scope))
        else:
            self._render_rows(query(store), scope)

    def _render_rows(self, result, scope):
        if result is None or not self.app.store or scope[:2] != (self.app.store.path, self.batch_id):
            return
        groups, unfiltered, pending, rules, counts, info, self._totals_cache = result
        self._counts(counts, info)
        self.overview.set(f'待核 {pending} 张 · 当前批次：{self.batch.get()}')
        if self.app.store.large_book() and not self._visible():
            self._rows_dirty = True
            return
        self._render_categories(self._category_counts(rules, unfiltered))
        total = len(groups)
        self.empty_initial.hide()
        if total:
            self.empty_results.hide()
        else:
            self.empty_results.show()
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
        self._result_scope = (*scope[:-1], self.result_offset)
        self._update_bulk_actions()
        modifier = '⌘' if self.tk.call('tk', 'windowingsystem') == 'aqua' else 'Ctrl'
        self.count_label.set(f'共 {total:,} 张 · 第 {self.result_offset // 500 + 1}/{max(1, (total + 499) // 500)} 页 · {modifier}/Shift 多选，双击看详情')

    def _actionable(self, group):
        return group['local_total'] > 0 and not any(row['stale'] for row in group['lines'])

    def _selected_keys(self):
        if self._result_scope is None:
            return ()
        return tuple(key for key in self.view.selection() if key in self.rows and self._actionable(self.rows[key]))

    def _update_bulk_actions(self, _event=None):
        count = len(self._selected_keys())
        skipped = len(self.view.selection()) - count if self._result_scope is not None else 0
        for button in (self.mark_button, self.join_button):
            button.configure(state='normal' if count else 'disabled')
        can_select = self._result_scope is not None and any(self._actionable(group) for group in self.rows.values())
        self.select_page_button.configure(state='normal' if can_select else 'disabled')
        self.selection_label.set(f'已选 {count} 张' + (f' · 跳过 {skipped} 张历史凭证' if skipped else ''))

    def _select_page(self, _event=None):
        if self._result_scope is not None:
            self.view.selection_set([key for key in self.view.get_children() if self._actionable(self.rows[key])])
            self.view.focus_set()
            self._update_bulk_actions()
        return 'break'

    def _mark_selected(self):
        keys = self._selected_keys()
        if keys:
            self.app.mark_voucher_keys(keys)

    def _join_selected(self):
        keys = self._selected_keys()
        if keys:
            self.app.workbench.choose_issue(keys)

    def open_detail(self, _event=None):
        if self._result_scope is None:
            return
        if _event is not None and getattr(_event, 'num', None) == 1:
            if self.view.identify_region(_event.x, _event.y) in ('tree', 'cell'):
                target = self.view.identify_row(_event.y)
                if target in self.rows:
                    ReviewDetail(self, self.rows[target])
            return
        selected = self.view.selection()
        if selected:
            target = self.view.focus()
            ReviewDetail(self, self.rows[target if target in selected else selected[0]])
            if _event is not None:
                return 'break'

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
        self.withdraw()
        from .ui import VIEW_COLUMNS, amount, tree
        from .voucher_notes import list_marks, mark_for_entry, remove_mark, save_mark, voucher_key
        self._mark_tools = (mark_for_entry, remove_mark, save_mark, list_marks)
        self.panel, self.app, self.group = panel, panel.app, group
        self.app.review_windows.add(self)
        self.bind('<Destroy>', lambda event: self.app.review_windows.discard(self) if event.widget is self else None, add='+')
        self.store = self.app.store
        self.entry_id = group['lines'][0]['entry_id'] if group.get('lines') else None
        current_lines = self.store.voucher_lines(self.entry_id) if self.entry_id is not None else []
        lines, self.historical = detail_lines(group, current_lines)
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
        history_notice = ('\n原凭证已变化或不存在；以下为发送时保存的只读快照。'
                          '关注、归集、忽略及复核等依赖当前凭证的操作已禁用。') if self.historical else ''
        wrapped_label(self, text=f"命中事项：{'、'.join(group['labels'])} · "
            f"规则 v{info['rule_id']} · 实际模型 {'、'.join(models) or '尚未调用'}{history_notice}\n{BOUNDARY}", padding=10, wraplength=760).grid(row=0, column=0, sticky='ew')
        self.detail_panes = ttk.Panedwindow(self, orient='vertical')
        self.detail_panes.grid(row=1, column=0, sticky='nsew', padx=10)
        self.voucher_area = ttk.Frame(self.detail_panes)
        self.detail_panes.add(self.voucher_area, weight=0)
        self.preview_tabs = ttk.Notebook(self.voucher_area)
        self.preview_tabs.pack(fill='both', expand=True)
        self.voucher_page = ttk.Frame(self.preview_tabs)
        self.preview_tabs.add(self.voucher_page, text='原凭证快照' if self.historical else '完整凭证')
        if self.historical:
            snapshot_page = ttk.Frame(self.preview_tabs)
            self.preview_tabs.add(snapshot_page, text='发送内容快照')
            read_only_text(snapshot_page, sent_snapshot_content(group), height=6)
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
        # 当前凭证与历史快照共用统一表格的框选复制和计算能力。
        from .table_tools import TableTools
        self.voucher_view = tree(self.voucher_page, VIEW_COLUMNS['voucher'])
        self.voucher_key = group['key'] if self.historical else (voucher_key(lines[0]) if lines else group['key'])
        self.cell_tools = TableTools(self.voucher_view, {'debit', 'credit', 'original'})
        if not self.historical:
            self.app.shortcuts.attach(self)
        self.voucher_view.configure(style="Compact.Treeview")
        self._voucher_rows = min(6, max(2, len(lines)))
        self.voucher_view.configure(height=self._voucher_rows)
        self.app._register_column_view('voucher', self.voucher_view)
        mark_for_entry, _remove_mark, _save_mark, _list_marks = self._mark_tools
        existing_mark = None if self.historical else mark_for_entry(self.store, self.entry_id)
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
        self.ignore_check = ttk.Checkbutton(self.actions, text='忽略此线索', variable=self.ignore_var, command=self._save_ignore)
        self.ignore_check.grid(row=0, column=2, sticky='w', padx=8)
        self.issue_button = ttk.Button(self.actions, text='加入尽调问题…', command=lambda: self.app.workbench.choose_issue([group['key']]))
        self.issue_button.grid(row=0, column=3, columnspan=2, sticky='e')
        self.calculator_button = ttk.Button(self.actions, text='计算器', command=self.cell_tools.toggle)
        self.calculator_button.grid(row=0, column=5, sticky='e', padx=(6, 0))
        self.close_button = ttk.Button(self.actions, text='关闭', command=self.request_close)
        self.close_button.grid(row=1, column=5, sticky='e', padx=(6, 0), pady=3)
        collapsed = '查看 / 编辑关注备注' if existing_mark else '关注这张凭证'
        self.note_toggle = ttk.Button(self.actions, text=collapsed, command=self._toggle_notes)
        self.note_toggle.grid(row=1, column=0, columnspan=2, sticky='w', pady=3)
        if self.historical:
            for widget in (self.ignore_check, self.issue_button, self.note_toggle):
                widget.configure(state='disabled')
        self.note_area = ttk.Frame(self.detail_panes, padding=(2, 8, 2, 0))
        self.attention = tk.BooleanVar(value=bool(existing_mark))
        self.attention_check = ttk.Checkbutton(self.note_area, text='关注这张凭证', variable=self.attention)
        self.attention_check.pack(anchor='w')
        if self.historical:
            self.attention_check.configure(state='disabled')
        self.note = ScrolledText(self.note_area, height=6, wrap='word', undo=True)
        self.note.pack(fill='both', expand=True)
        if existing_mark:
            self.note.insert('1.0', existing_mark['note'])
        elif self.historical:
            self.note.insert('1.0', '原凭证已变化或不存在；历史快照仅供查看，不能修改当前凭证的关注或备注。')
            self.note.configure(state='disabled')
        self._saved_note = (self.attention.get(), self.note.get('1.0', 'end-1c'))
        self.feedback = tk.StringVar(value='原凭证已变化或不存在；当前凭证操作已禁用。' if self.historical else '')
        self.note_key = self.voucher_key
        self.note_draft = None if self.historical else self.app.note_hub.bind(
            self, self.note, self.note_key, self.feedback, on_saved=self._note_saved)
        ttk.Label(self.note_area, textvariable=self.feedback, foreground='#555').pack(side='bottom', anchor='w', before=self.note.frame)
        self.note_save_button = ttk.Button(self.actions, text='保存关注与备注', command=self._save_attention)
        self.note_save_button.grid(row=1, column=3, sticky='e', pady=3)
        self.note_reload_button = ttk.Button(self.actions, text='重读备注…', command=lambda: self.app.note_hub.reload(self.note))
        self.note_reload_button.grid(row=1, column=2, sticky='w', padx=8)
        if self.historical:
            self.note_save_button.configure(state='disabled')
            self.note_reload_button.configure(state='disabled')
        self.note_save_button.grid_remove()
        self.note_reload_button.grid_remove()
        self.json_area = ttk.Frame(self.preview_tabs)
        self.json_text = None
        self.bind('<Escape>', lambda _event: self.request_close())
        self.protocol('WM_DELETE_WINDOW', self.request_close)
        center_window(self, self.app, 840, 560)
        self.deiconify()

    def request_close(self):
        if self.historical:
            self.destroy()
            return True
        if not self.note_draft.flush():
            self.note.focus_set()
            return False
        if self.attention.get() != self._saved_note[0] and not self._save_attention():
            return False
        self.destroy()
        return True

    def _note_saved(self):
        if self.historical:
            return
        from .issue_store import note_record
        current = note_record(self.store, self.note_key)
        pending_attention = self.attention.get() != self._saved_note[0]
        if not pending_attention:
            self.attention.set(bool(current))
        self._saved_note = (bool(current), self.note.get('1.0', 'end-1c'))
        for item in self.voucher_view.get_children():
            tags = tuple(tag for tag in self.voucher_view.item(item, 'tags') if tag != 'marked')
            self.voucher_view.item(item, tags=(('marked',) + tags) if current else tags)
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
        if self.historical:
            return
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
        if self.historical:
            self.feedback.set('原凭证已变化或不存在；请从当前明细重新打开凭证后再关注或复核。')
            return False
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
        if self.historical:
            self.feedback.set('原凭证已变化或不存在；历史结果仅供查看，不能修改复核状态。')
            return
        save_feedback(self.store, self.group['key'], 'ignore' if self.ignore_var.get() else '')
        self.panel.refresh_rows()
