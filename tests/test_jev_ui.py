import tempfile
import threading
import time
import gc
import tkinter as tk
from tkinter import ttk
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.jev_review import ReviewJob, batch_counts, prepare_batch, batch_info
from ledger_viewer.jev_rules import load_rules
from ledger_viewer.jev_ui import ConsentDialog, ReviewDetail, ReviewRulesDialog
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import list_marks
from test_jev_review import FakeClient, response


class ReviewUITest(unittest.TestCase):
    def setUp(self):
        # Reclaim prior Tk test cycles on the main thread before starting workers.
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        try:
            self.root = Application()
        except tk.TclError as exc:
            self.temp.cleanup()
            self.skipTest(str(exc))
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / '演示账套.sqlite')
        source = self.base / '虚构样例.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                          '2026-01-01,1,1002,银行存款,股东借款,100,0\n'
                          '2026-01-01,1,2241,其他应付款,股东借款,0,100\n', encoding='utf-8')
        header, _, mapping = guess_header(source, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(source, 'CSV', header, 'entry', mapping))
        self.panel = self.app.review_panel
        FakeClient.calls = []
        FakeClient.responder = staticmethod(lambda _state: response())
        self.root.update()

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def test_preview_cancel_never_starts_job_and_rule_editor_versions(self):
        batch = prepare_batch(self.app.store, {})
        with patch.object(ReviewJob, 'start') as start:
            dialog = ConsentDialog(self.panel, batch)
            self.root.update()
            text = dialog.preview.get('1.0', 'end')
            self.assertIn('股东借款', text)
            self.assertNotIn('虚构样例.csv', text)
            self.assertEqual(dialog.page_label.get(), '1 / 1')
            self.assertEqual(dialog.focus_get().cget('text'), '取消')
            dialog._cancel()
            start.assert_not_called()
        self.assertIsNone(batch_info(self.app.store, batch))
        old, _ = load_rules(self.app.store)
        editor = ReviewRulesDialog(self.app)
        self.root.update()
        editor.editor.insert('end', '\n仅为线索。')
        editor.thresholds['谨慎'].set('35')
        editor._save()
        new, rules = load_rules(self.app.store)
        self.assertGreater(new, old)
        self.assertEqual(rules['thresholds']['谨慎'], 35)
        self.assertTrue(rules['common'].endswith('仅为线索。'))

    def test_minimum_window_keeps_main_review_actions_visible(self):
        self.app.tabs.select(5)
        self.app.geometry('820x600')
        self.root.update()
        def walk(widget):
            for child in widget.winfo_children():
                yield child
                yield from walk(child)
        self.assertFalse(self.panel.options.winfo_ismapped())
        self.assertTrue(self.panel.progress_bar.winfo_ismapped())
        buttons = [w for w in walk(self.panel) if isinstance(w, ttk.Button) and w.winfo_ismapped()]
        self.assertTrue(buttons)
        for button in buttons:
            self.assertTrue(button.winfo_ismapped(), button.cget('text'))
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 8, button.cget('text'))
        preview = ConsentDialog(self.panel, prepare_batch(self.app.store, {}))
        preview.geometry('670x560')
        self.root.update()
        self.assertTrue(preview.confirm.winfo_ismapped())
        self.assertGreater(preview.preview.winfo_height(), 50)
        preview._cancel()

    def test_all_scope_ignores_detail_filters(self):
        self.app.account = 'C:2241'
        self.app.search.set('不存在的关键词')
        with patch.object(self.panel, 'prepare') as prepare:
            self.panel.new_batch()
        prepare.assert_called_once_with({})

    def test_progress_and_low_score_result_visible_before_all_done(self):
        extra = self.base / '第二张.csv'
        extra.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                         '2026-01-01,2,1002,银行存款,等待返回,10,0\n', encoding='utf-8')
        header, _, mapping = guess_header(extra, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(extra, 'CSV', header, 'entry', mapping))
        release = threading.Event()
        waiting = threading.Event()
        def respond(state):
            if state['summary'] == '等待返回':
                waiting.set()
                release.wait(8)
            return response({'loan': 0.2})
        FakeClient.responder = staticmethod(respond)
        batch = prepare_batch(self.app.store, {})
        job = ReviewJob(self.app.store.path, batch, 'fake-key', FakeClient)
        self.panel.batch_id = batch
        self.panel.lane.set('completed')
        self.panel.job = job
        try:
            job.start(self.app.store)
            self.panel.refresh()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.panel.view.get_children():
                self.root.update()
                time.sleep(0.02)
            self.assertTrue(waiting.is_set())
            self.assertTrue(job.thread.is_alive())
            self.assertEqual(len(self.panel.view.get_children()), 1)
            self.assertEqual(float(self.panel.progress_bar['value']), 1)
            self.assertEqual(float(self.panel.progress_bar['maximum']), 2)
            self.assertIn('50%', self.panel.progress.get())
            item = self.panel.view.get_children()[0]
            self.panel.view.selection_set(item)
            self.panel.refresh_rows()
            self.assertEqual(self.panel.view.selection(), (item,))
        finally:
            release.set()
            job.thread.join(5)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and len(self.panel.view.get_children()) < 2:
            self.root.update()
            time.sleep(0.02)
        self.assertEqual(len(self.panel.view.get_children()), 2)
        self.assertEqual(float(self.panel.progress_bar['value']), 2)
        self.assertEqual(batch_counts(self.app.store, batch)['successful_vouchers'], 2)

    def test_category_bar_click_filters_without_losing_counts(self):
        extra = self.base / '第二张.csv'
        extra.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                         '2026-01-02,2,1002,银行存款,股权转让款,100,0\n', encoding='utf-8')
        header, _, mapping = guess_header(extra, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(extra, 'CSV', header, 'entry', mapping))
        FakeClient.calls = []

        def respond(state):
            if '股权' in state.get('summary', ''):
                return response({'capital_equity': 0.9})
            return response({'financing': 0.9})

        FakeClient.responder = staticmethod(respond)
        batch = prepare_batch(self.app.store, {})
        job = ReviewJob(self.app.store.path, batch, 'fake-key', FakeClient)
        self.panel.batch_id = batch
        job.start(self.app.store)
        job.thread.join(5)
        self.panel.refresh()
        self.root.update()
        self.assertEqual(len(self.panel.view.get_children()), 2)
        rules = load_rules(self.app.store)[1]
        financing = next(topic['label'] for topic in rules['topics'] if topic['id'] == 'financing')
        equity = next(topic['label'] for topic in rules['topics'] if topic['id'] == 'capital_equity')
        # 点击单个分类：列表过滤为该分类，选中态保持，且计数条不塌缩（其他分类仍在）。
        button = next(b for b in self.panel.category_frame.winfo_children() if b.cget('text').startswith(financing))
        button.invoke()
        self.root.update()
        self.assertEqual(self.panel.category.get(), financing)
        self.assertEqual(len(self.panel.view.get_children()), 1)
        self.assertIn('股东借款', self.panel.view.item(self.panel.view.get_children()[0], 'values')[2])
        labels = [b.cget('text') for b in self.panel.category_frame.winfo_children()]
        self.assertTrue(any(text.startswith(equity) for text in labels))

    def test_confirm_results_detail_review_and_open_unlisted_voucher(self):
        batch = prepare_batch(self.app.store, {'account': 'C:2241'})
        dialog = ConsentDialog(self.panel, batch)
        original = ReviewJob.__init__
        def factory(job, path, batch_id, api_key):
            original(job, path, batch_id, api_key, FakeClient)
        with patch('ledger_viewer.jev_ui.get_api_key', return_value='fake-key'), patch.object(ReviewJob, '__init__', factory):
            dialog._confirm()
            self.panel.job.thread.join(5)
        self.panel.refresh()
        self.root.update()
        self.assertEqual(batch_counts(self.app.store, batch)['success'], 1)
        self.assertEqual(len(self.panel.view.get_children()), 1)
        group = next(iter(self.panel.rows.values()))
        self.assertTrue(group['partial'])
        detail = ReviewDetail(self.panel, group)
        self.root.update()
        # 凭证区与明细账双击打开的凭证同源：本凭证共 2 行分录（含未选入批次的 1002 行）。
        self.assertEqual(len(detail.voucher_view.get_children()), 2)
        # JSON 返回结果按需展开查看。
        detail._toggle_json()
        self.assertIn('完整结构化回答', detail.json_text.get('1.0', 'end'))
        detail._toggle_json()
        # 关注凭证：展开备注并保存，与凭证窗口的关注记录一致。
        detail._toggle_notes()
        detail.attention.set(True)
        detail.note.insert('1.0', '补充借款合同')
        detail._save_attention()
        marks = list_marks(self.app.store)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]['note'], '补充借款合同')
        detail.destroy()
        count = len(FakeClient.calls)
        self.panel.tier.set('宽松')
        self.panel._tier_change()
        self.panel.tier.set('谨慎')
        self.panel._tier_change()
        self.assertEqual(len(FakeClient.calls), count)


if __name__ == '__main__':
    unittest.main()
