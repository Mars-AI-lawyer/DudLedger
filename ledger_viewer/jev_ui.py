"""Tk review pages; all network work runs outside the GUI thread."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from queue import Empty, Queue
from threading import Thread

from .importer import format_money
from .dd_features import load_basis_overrides, load_config, materiality_bases, save_basis_overrides, save_config
from .jev_mapping import get_api_key
from .jev_rules import default_rules, load_rules, save_rules
from .related_parties import party_names, role_grouped_names
from .jev_review import (BOUNDARY, CATEGORY_SOURCE_LABELS, PRIORITY_LABELS, REVIEW_STATES, SEND_FIELDS,
    STATUS_LABELS, ReviewJob, active_job, batch_counts, batch_info, clear_category_override,
    discard_preview, is_uncertain, prepare_batch, preview_page, recover_interrupted,
    refresh_staleness, save_feedback, save_review, set_category_override, test_connection, voucher_results)

CONSENT_TEXT = ('本次将把所选账簿记录中的日期、科目、摘要、往来单位、辅助核算、非零一侧的借贷金额，'
    '以及当前账套尽调主体名单中的名称与身份（不含备注），发送至 TypeSafe 的 Jev 服务，'
    '用于筛选值得法律尽调律师进一步查看的凭证。\n\n'
    '同时随每张凭证发送程序预计算的“程序特征”事实：命中尽调主体名单、摘要关键词线索、'
    '金额关注线与占净资产／总资产／营业收入比例（如设置）、科目单笔金额分位、交易对手金额排名。'
    '这些字段可能包含客户商业信息或个人信息。请确认本次使用符合项目的保密安排，并仅选择本次审查所需的范围。'
    '原始账簿文件不会上传，具体发送内容可在下方逐页预览。\n\n'
    'AI 结果是尽调线索的查看优先级，不是法律风险评级；最终判断仍需结合相关材料由律师完成。')


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


def launch_connection_test(parent, status):
    key = get_api_key()
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
        self.title('Jev 审查规则与尽调筛选设置 · 当前账套')
        self.geometry('820x720')
        self.minsize(660, 560)
        self.transient(app)
        self.version, self.rules = load_rules(self.store)
        ttk.Label(self, text=f'当前规则 v{self.version} · jev-1.13.0 · 保存生成新版本，历史结果保留原规则', padding=10).pack(anchor='w')
        thresholds = ttk.LabelFrame(self, text='命中阈值（分类／整体判断概率×100，对应查看优先级）', padding=8)
        thresholds.pack(fill='x', padx=10)
        self.thresholds = {}
        for label in ('谨慎', '中', '宽松'):
            ttk.Label(thresholds, text=label).pack(side='left', padx=(5, 3))
            var = tk.StringVar(value=str(self.rules['thresholds'][label]))
            self.thresholds[label] = var
            ttk.Entry(thresholds, textvariable=var, width=7).pack(side='left', padx=(0, 16))
        ttk.Label(self, text='必须保持：谨慎 < 中 < 宽松。任一分类或整体判断达到“谨慎”即进入待核线索；'
            f'达到“宽松”显示为“{PRIORITY_LABELS["宽松"]}”、“中”为“{PRIORITY_LABELS["中"]}”、'
            f'“谨慎”为“{PRIORITY_LABELS["谨慎"]}”。查看优先级反映尽调相关程度，不是法律风险评级，'
            '概率也不代表违法事实已获证实。', padding=10).pack(anchor='w')
        self._build_filter_settings()
        self.paths = {'通用判断边界': ('common',), '整体是否值得查看 · 提问': ('review',), '信息不足提问': ('insufficient',)}
        for i, topic in enumerate(self.rules['topics']):
            self.paths[f"{topic['label']} · 提问"] = ('topics', i, 'question')
            self.paths[f"{topic['label']} · 核查建议"] = ('topics', i, 'advice')
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

    def _build_filter_settings(self):
        config = load_config(self.store)
        bases = materiality_bases(self.store)
        overrides = load_basis_overrides(self.store)
        frame = ttk.LabelFrame(self, text='尽调筛选设置（本地计算“程序特征”随每张凭证发送；金额口径是律师筛选参数，不是审计重大性标准）', padding=8)
        frame.pack(fill='x', padx=10, pady=(0, 4))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.filter_vars = {}
        ttk.Label(row, text='金额关注线（元）').pack(side='left', padx=(5, 3))
        amount_var = tk.StringVar(value=str(config['amount_yuan']) if config['amount_yuan'] is not None else '')
        self.filter_vars['amount_yuan'] = amount_var
        ttk.Entry(row, textvariable=amount_var, width=12).pack(side='left', padx=(0, 6))
        ttk.Label(row, foreground='#555', text='默认不设；填写后达到该金额的凭证会标注“达到金额关注线”，机械凭证也会因此恢复送审').pack(side='left')
        detail_row = ttk.Frame(frame)
        detail_row.pack(fill='x', pady=(5, 0))
        for label, key, width in (
            ('净资产关注比例 %', 'net_asset_pct', 8),
            ('总资产关注比例 %', 'asset_pct', 8),
            ('营业收入关注比例 %', 'revenue_pct', 8),
        ):
            ttk.Label(detail_row, text=label).pack(side='left', padx=(5, 3))
            var = tk.StringVar(value=str(config[key]))
            self.filter_vars[key] = var
            ttk.Entry(detail_row, textvariable=var, width=width).pack(side='left', padx=(0, 10))
        ttk.Label(detail_row, text='科目分位线').pack(side='left', padx=(5, 3))
        self.percentile = tk.StringVar(value='P99' if config['percentile'] >= 0.99 else 'P95')
        ttk.Combobox(detail_row, textvariable=self.percentile, values=('P95', 'P99'), state='readonly', width=5).pack(side='left')
        basis_row = ttk.Frame(frame)
        basis_row.pack(fill='x', pady=(6, 0))
        detected = []
        for key, title in (('net_assets', '净资产'), ('total_assets', '总资产'), ('revenue', '营业收入')):
            detected.append(f"{title} {format_money(bases[key])}" if key in bases else f"{title} 未取得")
        ttk.Label(basis_row, text='占比基数自动取最新资产负债表／利润表：' + '；'.join(detected)
            + '。可手动覆盖（元，留空用自动值）：', wraplength=760).pack(anchor='w')
        fields = ttk.Frame(basis_row)
        fields.pack(anchor='w')
        self.basis_vars = {}
        for key, title in (('net_assets', '净资产'), ('total_assets', '总资产'), ('revenue', '营业收入')):
            ttk.Label(fields, text=title).pack(side='left', padx=(5, 3))
            var = tk.StringVar(value=str(overrides[key] // 100) if key in overrides else '')
            self.basis_vars[key] = var
            ttk.Entry(fields, textvariable=var, width=14).pack(side='left', padx=(0, 12))
        ttk.Label(frame, foreground='#555', text='折旧／摊销／汇兑等机械凭证默认不送 AI，但命中主体名单、摘要线索或达到金额关注线（如设置）时会自动恢复送审。',
            wraplength=760).pack(anchor='w', pady=(4, 0))

    def _parse_number(self, var, title, minimum=0.0):
        try:
            value = float(var.get())
        except (TypeError, ValueError):
            raise ValueError(f'{title}必须是数字')
        if value < minimum:
            raise ValueError(f'{title}不能小于 {minimum}')
        return value

    def _save_filter_settings(self):
        from .importer import money
        amount_text = self.filter_vars['amount_yuan'].get().strip()
        config = {'amount_yuan': None}  # 留空＝默认不设金额关注线
        if amount_text:
            config['amount_yuan'] = self._parse_number(self.filter_vars['amount_yuan'], '金额关注线')
            if config['amount_yuan'] <= 0:
                raise ValueError('金额关注线必须是正数，或留空表示不设')
        for key, title in (('net_asset_pct', '净资产关注比例'), ('asset_pct', '总资产关注比例'),
                           ('revenue_pct', '营业收入关注比例')):
            config[key] = self._parse_number(self.filter_vars[key], title)
        config['percentile'] = 0.99 if self.percentile.get() == 'P99' else 0.95
        save_config(self.store, config)
        overrides = {}
        for key, var in self.basis_vars.items():
            text = var.get().strip()
            if not text:
                continue
            try:
                cents = money(text)
            except ValueError as exc:
                raise ValueError(f'占比基数：{exc}') from None
            if cents <= 0:
                raise ValueError('占比基数只能填正数金额（元），留空表示自动读取')
            overrides[key] = cents
        save_basis_overrides(self.store, overrides)

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
        for label, var in self.thresholds.items():
            var.set(str(self.rules['thresholds'][label]))
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', self._value(self.paths[self.current]))

    def _save(self):
        self._stash()
        try:
            self._save_filter_settings()
            self.rules['thresholds'] = {label: float(var.get()) for label, var in self.thresholds.items()}
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
        self.title('使用 Jev 云端辅助审查')
        self.geometry('810x730')
        self.minsize(670, 560)
        self.transient(panel.app)
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        info = batch_info(self.store, batch_id)
        scope = json.loads(info['scope_json'])
        description = []
        labels = {'account': '科目', 'search': '关键词', 'fiscal_year': '年度', 'period': '期间', 'source_ids': '来源编号', 'include_descendants': '包含下级科目'}
        for field, value in scope.items():
            if value:
                description.append(f'{labels[field]}：{value}')
        wrapped_label(self, text=CONSENT_TEXT, wraplength=750, padding=12).pack(fill='x')
        wrapped_label(self, text=f'账套：{self.store.path.name}\n范围：' + ('；'.join(description) or '全部明细分录') + f'\n共 {self.total} 条分录，归为 {self.voucher_total} 张凭证／无号来源行；每张凭证去重合并后核查（相同行合并金额并标注次数，仅发送非零一侧金额）；超长内容分段 · 规则 v{info["rule_id"]} · {info["model"]}', padding=(12, 4), wraplength=750).pack(fill='x')
        parties = party_names(self.store.related_parties())
        grouped = role_grouped_names(self.store.related_parties())
        party_preview = '；'.join(f'{role}：{"、".join(names[:4])}' for role, names in grouped.items())
        if len(party_preview) > 220:
            party_preview = party_preview[:220] + '…'
        wrapped_label(self, text='完整发送字段：' + '、'.join(SEND_FIELDS.values()) + f'（空白字段与零金额不发送）'
            + (f'\n尽调主体名单：共 {len(parties)} 个名称，按身份分组随每张凭证一并发送（仅名称、别名与身份，不含备注）：{party_preview}' if parties else '\n当前账套未设置尽调主体名单；可在「查看 → 尽调主体名单」中维护或批量导入。')
            + '\n程序特征：命中主体名单、摘要关键词线索、金额关注线与占比、科目单笔分位、交易对手排名等本地事实。'
            + '\n文件名、路径、制单人、原始行及凭证标识只在本地关联，不发送。', padding=12, wraplength=750).pack(fill='x')
        wrapped_label(self, text=f"已排除结转损益／低价值机械凭证 {counts['skipped_vouchers']} 张（仍可在结果中查看，出现异常特征会自动恢复送审）；只核查其余凭证。", padding=(12, 0)).pack(fill='x')
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
        text = '\n\n'.join(f'第 {self.page * 20 + index} 条' + ('（结转损益，不发送）' if row['status'] == 'skipped' else '') + f'\n{display_input(json.loads(row["input_json"]))}' for index, row in enumerate(rows, 1))
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
            job = ReviewJob(self.store.path, self.batch_id, get_api_key())
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
        self.job = None
        self.rows = {}
        self.batch_options = {}
        self.category_ids = {}
        self.pack(fill='both', expand=True)
        actions = ttk.Frame(self, padding=6)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='筛选全部凭证', command=self.new_batch, style='Accent.TButton')
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text='停止', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=5)
        self.more_button = ttk.Button(actions, text='更多选项', command=self._toggle_options)
        self.more_button.pack(side='right')
        self.progress = tk.StringVar(value='筛选当前账套的全部凭证：程序先算特征，Jev 判断是否值得律师查看，完成一张即显示一张。')
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
        ttk.Button(batch_row, text='审查规则与筛选设置', command=app.open_review_rules).pack(side='right', padx=5)
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
        self.tier, self.account, self.period = (tk.StringVar(value=v) for v in ('谨慎', '', '全部期间'))
        self.show_ignored = tk.BooleanVar(value=False)
        ttk.Label(filters, text='范围').pack(side='left')
        tier = ttk.Combobox(filters, textvariable=self.tier, values=('宽松', '中', '谨慎'), state='readonly', width=6)
        tier.pack(side='left', padx=5)
        tier.bind('<<ComboboxSelected>>', self._tier_change)
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
        self.view = tree(self, [('voucher', '凭证', 140), ('amount', '金额', 110), ('summary', '摘要', 200),
            ('categories', '尽调分类', 190), ('priority', '查看优先级', 80), ('score', '相关度', 65),
            ('analysis', '状态', 110)])
        self.view.bind('<Double-1>', self.open_detail)
        bottom = ttk.Frame(self, padding=6)
        bottom.pack(side='bottom', fill='x', before=self.view.master)
        self.count_label = tk.StringVar()
        wrapped_label(bottom, textvariable=self.count_label).pack(side='left', fill='x', expand=True)
        ttk.Button(bottom, text='查看详情', command=self.open_detail).pack(side='right')
        self._poll_id = self.after(200, self._poll)
        self.bind("<Destroy>", self._destroyed, add="+")

    def _render_category_bar(self, rules, counts):
        for child in self.category_frame.winfo_children():
            child.destroy()
        self.category_ids = {'全部': ''}
        selected = self.category.get() or '全部'
        if selected not in counts and selected != '全部':
            selected = '全部'
        for index, (label, count) in enumerate(counts):
            button = ttk.Radiobutton(self.category_frame, text=f'{label} {count}', value=label,
                variable=self.category, command=self.refresh_rows)
            button.grid(row=index // 7, column=index % 7, sticky='w', padx=(0, 10), pady=1)
        self.category.set(selected)

    def _category_counts(self, rules, candidates):
        counts = [('全部', len(candidates))]
        unclassified = 0
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
        if active_job(self.app.store.path):
            messagebox.showinfo('审查正在运行', '当前账套同一时间只运行一个批次。', parent=self)
            return
        self.prepare({})

    def prepare(self, scope):
        try:
            batch_id = prepare_batch(self.app.store, scope)
        except Exception as exc:
            messagebox.showerror('无法生成预览', str(exc), parent=self)
            return
        ConsentDialog(self, batch_id)

    def cancel(self):
        if self.app.store:
            job = active_job(self.app.store.path)
            if job:
                job.cancel.set()
                self.progress.set('已请求取消；正在发出的请求结束后保留已完成结果，停止后续发送。')

    def _select_batch(self, _event=None):
        self.batch_id = self.batch_options.get(self.batch.get())
        self.refresh()

    def _tier_change(self, _event=None):
        if self.app.store:
            self.app.store.save_setting('jev_tier', self.tier.get())
        self.refresh_rows()

    def refresh(self):
        store = self.app.store
        if not store:
            return
        recover_interrupted(store)
        batches = store.db.execute("SELECT * FROM jev_batches WHERE status<>'preview' ORDER BY id DESC").fetchall()
        self.batch_options = {f"#{r['id']} · {r['created_at']} · {STATUS_LABELS.get(r['status'], r['status'])} · 规则 v{r['rule_id']}": r['id'] for r in batches}
        self.batch_combo.configure(values=list(self.batch_options))
        if self.batch_id not in self.batch_options.values():
            self.batch_id = batches[0]['id'] if batches else None
        self.batch.set(next((label for label, value in self.batch_options.items() if value == self.batch_id), '尚无审查批次'))
        self.tier.set(store.setting('jev_tier', '谨慎'))
        if self.batch_id is not None:
            refresh_staleness(store, self.batch_id)
            self._counts()
        self.refresh_rows()

    def _counts(self):
        counts = batch_counts(self.app.store, self.batch_id)
        info = batch_info(self.app.store, self.batch_id)
        running = active_job(self.app.store.path) is not None
        self.start_button.configure(state='disabled' if running else 'normal')
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

    def refresh_rows(self):
        old_items = set(self.view.get_children())
        self.rows = {}
        if not self.app.store or self.batch_id is None:
            self.view.delete(*old_items)
            self.progress_bar.configure(maximum=1, value=0)
            self.count_label.set('尚无结果，点击“筛选全部凭证”开始。')
            for child in self.category_frame.winfo_children():
                child.destroy()
            return
        info = batch_info(self.app.store, self.batch_id)
        rules = json.loads(info['body_json']) if info else None
        show_ignored = self.show_ignored.get()
        category = self.category_ids.get(self.category.get(), '') if rules else ''
        groups = voucher_results(self.app.store, self.batch_id, self.tier.get(), self.lane.get(), self.account.get().strip(),
            category, '' if self.period.get() == '全部期间' else self.period.get(), show_ignored)
        if rules:
            candidates = groups if self.lane.get() == 'candidates' else voucher_results(
                self.app.store, self.batch_id, self.tier.get(), 'candidates', self.account.get().strip(),
                '', '' if self.period.get() == '全部期间' else self.period.get(), show_ignored)
            self._render_category_bar(rules, self._category_counts(rules, candidates))
        periods = [r[0] for r in self.app.store.db.execute("SELECT DISTINCT json_extract(local_json,'$.fiscal_year') || '/' || json_extract(local_json,'$.period') FROM jev_results WHERE batch_id=?", (self.batch_id,))]
        self.period_combo.configure(values=['全部期间', *sorted(periods)])
        for index, group in enumerate(groups):
            item = group['key']
            self.rows[item] = group
            row = group['hits'][0]['local']
            voucher = f"{row['fiscal_year']}/{row['period']} {row['voucher_type']}{row['voucher_no']}" if row['voucher_no'] else f"无凭证号 · {row['filename']}:{row['row_no']}"
            states = list(dict.fromkeys('已过期' if r['stale'] else STATUS_LABELS[r['status']] for r in group['lines']))
            if any(r['status'] == 'success' and is_uncertain(r) for r in group['lines']):
                states.append('需人工复核')
            if group['feedback'] == 'ignore':
                states.append('已忽略')
            if group['partial']:
                states.append('仅覆盖部分分录')
            currencies = group['sort_currency']
            amount = f"{format_money(group['sort_amount'])} {currencies[0]}" if len(currencies) == 1 and currencies[0] != '币种未注明' else (f"{format_money(group['sort_amount'])}" if len(currencies) == 1 else '多币种')
            values = (voucher, amount, '；'.join(dict.fromkeys(r['local']['summary'] for r in group['hits'])),
                '、'.join(group['labels']), group['priority'],
                f"{group['score']:.1f}" if group['score'] is not None else '—', '、'.join(states))
            if item in old_items:
                self.view.item(item, values=values)
                old_items.remove(item)
            else:
                self.view.insert('', 'end', iid=item, values=values)
            self.view.move(item, '', index)
        self.view.delete(*old_items)
        self.count_label.set(f'显示 {len(groups)} 张 · 双击查看分类依据、完整凭证并复核。查看优先级是尽调相关程度，不是法律风险评级。')

    def open_detail(self, _event=None):
        selected = self.view.selection()
        if selected:
            ReviewDetail(self, self.rows[selected[0]])

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
                if done:
                    self.job = None
                    self.refresh()
                elif changed and self.batch_id == job.batch_id:
                    self._counts()
                    self.refresh_rows()
        self._poll_id = self.after(250, self._poll)


class ReviewDetail(tk.Toplevel):
    def __init__(self, panel, group):
        super().__init__(panel)
        from .ui import tree
        self.panel, self.app, self.group = panel, panel.app, group
        self.store = self.app.store
        self.title('AI 尽调线索 · 分类与人工复核')
        self.geometry('940x780')
        self.minsize(720, 600)
        info = batch_info(self.store, panel.batch_id)
        self.rules = json.loads(info['body_json'])
        effective = {identifier: sources for identifier, _label, sources, _probability in group['categories']}
        categories_text = '、'.join(group['labels'])
        wrapped_label(self, text=f"规则 v{info['rule_id']} · 请求模型 {info['model']} · 查看优先级：{group['priority'] or '—'}\n"
            f"尽调分类：{categories_text} · {len(group['lines'])} 条选定分录 / 本地凭证 {group['local_total']} 条\n{BOUNDARY}", padding=10, wraplength=860).pack(fill='x')
        self.lines = {str(r['id']): r for r in group['lines']}
        holder = ttk.Frame(self)
        holder.pack(fill='x')
        self.view = tree(holder, [('account', '科目', 180), ('summary', '账面摘要', 300), ('amount', '金额', 100), ('score', '相关度', 70), ('source', '来源行', 200)])
        self.view.configure(height=4)
        for key, r in self.lines.items():
            local = r['local']
            side = format_money(max(abs(local['debit_cents']), abs(local['credit_cents']))) if max(abs(local['debit_cents']), abs(local['credit_cents'])) else ''
            self.view.insert('', 'end', iid=key, values=(f"{local['code']} {local['name']}", local['summary'],
                side, f"{r['score']:.1f}" if r['score'] is not None else '—', f"{local['filename']}/{local['sheet']}:{local['row_no']}"))
        self.view.bind('<<TreeviewSelect>>', self._show)
        categories = ttk.LabelFrame(self, text='尽调分类（一张凭证可属多个分类；勾选调整仅记录律师意见，可随时改回）', padding=6)
        categories.pack(fill='x', padx=8, pady=(6, 0))
        self.category_vars = []
        for index, topic in enumerate(self.rules['topics']):
            sources = effective.get(topic['id'])
            var = tk.BooleanVar(value=bool(sources))
            mark = '·' + '/'.join(sorted(CATEGORY_SOURCE_LABELS[s] for s in sources)) if sources else ''
            button = ttk.Checkbutton(categories, text=topic['label'] + mark, variable=var)
            button.grid(row=index // 5, column=index % 5, sticky='w', padx=(0, 10))
            self.category_vars.append((topic['id'], var, bool(sources), sources or set()))
        ttk.Label(self, text='关注依据（程序预计算事实与 AI 判断线索，非违法认定）：', padding=(10, 4)).pack(anchor='w')
        reasons = '\n'.join(f'✓ {fact}' for fact in group['reasons']) or '（无程序特征提示）'
        self.text = read_only_text(self, reasons, height=9)
        footer = ttk.Frame(self)
        footer.pack(side='bottom', fill='x', before=self.text.frame)
        actions = ttk.Frame(footer, padding=8)
        actions.pack(fill='x')
        ttk.Button(actions, text='打开本地同号凭证／加入关注', command=self._voucher).pack(side='left')
        self.ignore_var = tk.BooleanVar(value=group['feedback'] == 'ignore')
        ttk.Checkbutton(actions, text='忽略此线索（不再出现在待核列表，仅保存反馈）', variable=self.ignore_var).pack(side='left', padx=(14, 0))
        ttk.Label(actions, text='人工复核').pack(side='left', padx=(18, 5))
        self.review_status = tk.StringVar(value=group['review']['status'])
        ttk.Combobox(actions, textvariable=self.review_status, values=REVIEW_STATES, state='readonly', width=15).pack(side='left')
        ttk.Label(footer, text='核查备注（保存在当前账套，来源变化后仍保留）', padding=(8, 2)).pack(anchor='w')
        self.note = ScrolledText(footer, height=4, wrap='word', undo=True)
        self.note.pack(fill='x', padx=8)
        self.note.insert('1.0', group['review']['note'])
        ttk.Button(footer, text='保存分类与复核', command=self._save, style='Accent.TButton').pack(anchor='e', padx=8, pady=8)
        self.view.selection_set(next(iter(self.lines)))
        self._show()

    def _selected(self):
        selected = self.view.selection()
        return self.lines[selected[0]] if selected else None

    def _show(self, _event=None):
        row = self._selected()
        if row is None:
            return
        local = row['local']
        lines = [f"账面记载：{local['summary']}", f"分析状态：{'已过期' if row['stale'] else STATUS_LABELS[row['status']]}",
            f"来源：{local['original_path']} / {local['sheet']} 第 {local['row_no']} 行", f"来源 SHA-256：{local['sha256']}",
            f"借方：{format_money(local['debit_cents'])}；贷方：{format_money(local['credit_cents'])}；币种：{local['currency'] or '未注明'}；原币金额：{local['original_amount']}"]
        if row['status'] == 'skipped':
            lines += [f"未送 AI：{row['error']}"]
        if row['response_json']:
            data = json.loads(row['response_json'])
            answers = data['answers']
            strongest = max((value['noul'] for key, value in answers.items() if key != 'insufficient'), default=0)
            lines += [f"判断单位：{'凭证整体' if data.get('review_unit') == 'voucher' else '分录'}；分段数：{data.get('chunks', 1)}\n实际模型：{data['model']}；相关度：{row['score']:.1f}（任一分类或整体判断的最高概率，非加权平均）；信息不足：{row['insufficient']:.3f}",
                      f"同次请求用量（{data.get('shared_entries', 1)} 条分录共用，不逐行累加）：{data['usage']}；{'缓存复用，未再次请求' if row['cached'] else '本次请求'}", '']
            if 'review' in answers:
                lines += [f"整体判断（是否值得律师查看）：{answers['review']['noul']:.3f}", '']
            if data.get('chunks', 1) > 1:
                lines += ['分段结果：各分类信号取片段最大值，不是整张凭证的总体概率；需人工结合全文复核。', '']
            for topic in self.rules['topics']:
                probability = answers[topic['id']]['noul']
                if probability >= 0.35:
                    lines += [f"命中分类：{topic['label']}（{probability:.3f}{'，分类待确认' if probability < 0.65 else ''}）", f"建议核查：{topic['advice']}", '']
            if is_uncertain(row):
                lines += ['信息不足／判断不确定／已分段：请核对整张凭证的交易用途；不确定未降低分类概率。', '']
            grouped = role_grouped_names(self.store.related_parties())
            count = sum(len(names) for names in grouped.values())
            preview = '；'.join(f'{role}：{"、".join(names[:4])}' for role, names in grouped.items())
            if len(preview) > 160:
                preview = preview[:160] + '…'
            lines += [f"随本凭证发送的尽调主体名单（按身份分组）：共 {count} 个名称" + (f"（{preview}）" if preview else "；可在「查看 → 尽调主体名单」维护"), '']
            lines += ['完整结构化回答：', json.dumps(answers, ensure_ascii=False, indent=2), '']
        elif row['error']:
            lines += [row['error']]
        lines += [BOUNDARY, '', '当前行的精简输入（发送时与同凭证其他行合并去重：内容相同的行金额合并并标注次数；空白字段与零金额不发送）：' if row['status'] != 'skipped' else '已跳过，以下字段未发送：', display_input(json.loads(row['input_json'])), '',
                  '本地保留的原始行（不发送）：', local['raw_json']]
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', '\n'.join(lines))
        self.text.configure(state='disabled')

    def _voucher(self):
        row = self._selected()
        if row is None:
            return
        refresh_staleness(self.store, self.panel.batch_id)
        stale = self.store.db.execute('SELECT stale FROM jev_results WHERE id=?', (row['id'],)).fetchone()[0]
        if stale:
            messagebox.showinfo('来源已变化', '这里保留的是历史快照。请到明细账定位当前分录，重新核实；原复核备注仍保留。', parent=self)
            return
        self.app.open_voucher(None, entry_id=row['entry_id'])

    def _save(self):
        key = self.group['key']
        for identifier, var, was, sources in self.category_vars:
            checked = var.get()
            if checked and not was:
                set_category_override(self.store, key, identifier, 'add')
            elif not checked and was:
                if sources == {'user'}:
                    clear_category_override(self.store, key, identifier)
                else:
                    set_category_override(self.store, key, identifier, 'remove')
        save_feedback(self.store, key, 'ignore' if self.ignore_var.get() else '')
        save_review(self.store, key, self.review_status.get(), self.note.get('1.0', 'end-1c'))
        self.panel.refresh_rows()
        self.destroy()
