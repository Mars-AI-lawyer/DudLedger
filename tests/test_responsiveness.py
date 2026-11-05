"""Regression coverage for selection, paging, cancellation and editing state."""
import csv
import gc
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch
from tkinter import ttk

from ledger_viewer.background import Cancelled
from ledger_viewer.importer import classify_sheet
from ledger_viewer.jev_review import (prepare_batch, voucher_result_context,
                                      voucher_results, voucher_totals)
from ledger_viewer.precheck import precheck
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import Application, BatchImportDialog, ImportDialog
from ledger_viewer.voucher_notes import list_marks, voucher_key


class ResponsivenessTest(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch('ledger_viewer.ui.Path.home', return_value=self.base):
            self.root = Application()
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / 'test.sqlite')
        self.source = self.base / 'test.csv'
        self.source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                               '2026-01-01,1,1002,银行存款,业务,1,0\n'
                               '2026-01-01,1,2001,应付款,业务,0,1\n', encoding='utf-8')
        self.spec = classify_sheet(self.source, 'CSV')
        self.app.store.import_sheet(self.spec)
        self.app.refresh_all()
        self.root.update()

    def tearDown(self):
        with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=False):
            for window in list(self.root.windows):
                window._close()
        self.temp.cleanup()

    def wait_for(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(predicate(), 'background work did not finish')

    def add_entries(self, count):
        store = self.app.store
        source = store.sources()[0]['id']
        store.db.executemany('''INSERT INTO entries(source_id,row_no,entry_date,fiscal_year,period,
            voucher_no,voucher_type,account_key,summary,counterparty,auxiliary,debit_cents,credit_cents,raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ((source, index + 10, '2026-01-01', '2026', '1', str(index + 10), '', 'C:1002',
              'needle' if index >= count - 3 else 'other', '', '', index, 0, '{}') for index in range(count)))
        store._touch_ledger()
        store.db.commit()

    def test_collapse_and_expand_recompute_selection_but_not_calculation_history(self):
        view, tools = self.app.summary_view, self.app.balance_tools
        view.delete(*view.get_children())
        for parent, key, amount in (('', 'p', '1.00'), ('p', 'child', '2.00'), ('', 'q', '3.00')):
            view.insert(parent, 'end', iid=key, open=True, values=('', '', '', '', amount))
        tools.select('p', 'pd')
        tools.select('q', 'pd', extend=True)
        tools.calculate(1)
        self.assertEqual(tools.selection_total.get(), '6.00')
        view.item('p', open=False)
        self.app._expansion_labels()
        self.root.update()
        self.assertEqual(tools.selection_total.get(), '4.00')
        self.assertEqual(tools.result.get(), '6.00')
        view.item('p', open=True)
        self.app._expansion_labels()
        self.root.update()
        self.assertEqual(tools.selection_total.get(), '6.00')

    def test_grid_covers_children_and_forwards_pointer_events(self):
        view = self.app.summary_view
        view.delete(*view.get_children())
        view.insert('', 'end', iid='p', open=True, values=('1', 'parent'))
        view.insert('p', 'end', iid='c', values=('11', 'child'))
        self.root.update()
        grid = view.dashed_grid
        grid.draw()
        self.root.update_idletasks()
        positions = {int(line.place_info()['y']) for line in grid.horizontal if line.place_info()}
        self.assertIn(view.bbox('c')[1] + view.bbox('c')[3] - 1, positions)
        forwarded = []
        view.bind('<MouseWheel>', lambda event: forwarded.append(event.delta))
        line = next(line for line in grid.horizontal if line.place_info())
        line.event_generate('<MouseWheel>', delta=-1, x=5, y=0)
        self.root.update()
        self.assertEqual(forwarded, [-1])

    def open_editor(self):
        self.app.open_voucher(None, entry_id=self.app.store.entries()[0]['id'], edit_attention=True)
        self.root.update()
        dialog = next(iter(self.app.voucher_windows))
        dialog.note_editor.insert('end', '待核对原始合同')
        return dialog

    def test_unsaved_note_cancel_blocks_next_voucher_and_book_exit(self):
        dialog = self.open_editor()
        with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=None):
            self.app.open_voucher(None, entry_id=self.app.store.entries()[0]['id'])
            self.assertEqual(self.app.voucher_windows, {dialog})
            self.app.close_book()
            self.assertIsNotNone(self.app.store)
            self.assertTrue(dialog.winfo_exists())
        with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=True):
            self.assertTrue(dialog.request_close())
        self.assertEqual(list_marks(self.app.store)[0]['note'], '待核对原始合同')

    def test_failed_save_keeps_note_and_dialog(self):
        dialog = self.open_editor()
        with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=True), \
             patch('ledger_viewer.ui.save_mark', side_effect=OSError('write failed')), \
             patch('ledger_viewer.ui.messagebox.showerror'):
            self.assertFalse(dialog.request_close())
        self.assertTrue(dialog.winfo_exists())
        self.assertIn('待核对', dialog.note_editor.get('1.0', 'end'))
        with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=False):
            self.assertTrue(dialog.request_close())
        self.assertEqual(list_marks(self.app.store), [])

    def test_paging_covers_all_matches_and_retains_committed_calculation(self):
        self.add_entries(1103)
        self.app.refresh_details()
        first = self.app.detail_view.get_children()
        self.assertEqual(len(first), 500)
        self.app.detail_tools.select(first[0], 'debit')
        self.app.detail_tools.calculate(1)
        total = self.app.detail_tools.result.get()
        self.app.change_detail_page(1)
        second = self.app.detail_view.get_children()
        self.assertEqual(len(second), 500)
        self.assertFalse(set(first) & set(second))
        self.assertIsNone(self.app.detail_tools.active)
        self.assertEqual(self.app.detail_tools.result.get(), total)
        self.app.change_detail_page(1)
        self.assertEqual(len(self.app.detail_view.get_children()), 105)
        self.assertEqual(self.app.detail_total, 1105)
        self.app.search.set('needle')
        self.app.submit_detail_search()
        self.assertEqual(self.app.detail_offset, 0)
        self.assertEqual(self.app.detail_total, 3)
        output = self.base / 'export.csv'
        self.assertEqual(self.app.store.export_entries(output), 1105)
        with output.open(encoding='utf-8-sig') as handle:
            self.assertEqual(len(list(csv.reader(handle))), 1106)

    def test_page_sort_and_period_scope_match_existing_query(self):
        self.add_entries(1050)
        for descending in (False, True):
            filters = dict(sort_by='debit', descending=descending, period_from=('2026', '1'), period_to=('2026', '1'))
            rows, total, offset = self.app.store.entries_page(offset=500, **filters)
            expected = self.app.store.entries(limit=2000, **filters)
            self.assertEqual(total, len(expected))
            self.assertEqual([row['id'] for row in rows], [row['id'] for row in expected[500:1000]])
        rows, total, offset = self.app.store.entries_page(offset=100000)
        self.assertEqual(offset, 1000)
        self.assertEqual(len(rows), 52)

    def test_summary_search_does_not_refresh_other_pages_or_run_checks(self):
        with patch.object(self.app, 'refresh_details') as details, \
             patch.object(self.app, 'refresh_marks') as marks, \
             patch('ledger_viewer.ui.run_checks') as checks:
            self.app.summary_search.set('银行')
            self.app.refresh_summary()
        details.assert_not_called()
        marks.assert_not_called()
        checks.assert_not_called()
        self.assertEqual(self.app.summary_view.get_children(), ('C:1002',))

    def test_checks_cache_invalidates_only_when_sources_or_plan_change(self):
        self.app.refresh_checks()
        with patch('ledger_viewer.ui.run_checks', wraps=__import__('ledger_viewer.checks', fromlist=['run_checks']).run_checks) as checks:
            self.app.store.save_setting('unrelated_display_setting', 'x')
            self.app.refresh_checks()
            checks.assert_not_called()
            self.app.store.delete_source(self.app.store.sources()[0]['id'])
            self.app.refresh_checks()
            checks.assert_called_once()

    def test_background_superseded_result_is_not_applied_and_callback_is_on_ui_thread(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        applied = []
        main = threading.get_ident()
        def old(cancelled, report):
            started.set()
            release.wait(3)
            finished.set()
            return 'old'
        self.app.tasks.submit('race', old, lambda result: applied.append(result))
        self.wait_for(started.is_set)
        self.app.tasks.submit('race', lambda cancel, report: 'new',
                              lambda result: applied.append((result, threading.get_ident())))
        release.set()
        self.wait_for(lambda: bool(applied) and finished.is_set())
        self.assertEqual(applied, [('new', main)])

    def test_leaving_book_drops_late_read_result(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        applied = []
        def work(cancelled, report):
            started.set()
            release.wait(3)
            finished.set()
            return 'late'
        self.app.tasks.submit('late', work, applied.append)
        self.wait_for(started.is_set)
        self.app.close_book()
        release.set()
        self.wait_for(finished.is_set)
        self.root.update()
        self.assertEqual(applied, [])
        self.assertIsNone(self.app.store)

    def test_partial_import_success_is_unchecked_and_failure_can_retry(self):
        other = self.base / 'other.csv'
        other.write_text(self.source.read_text().replace('业务', '另一笔'), encoding='utf-8')
        dialog = BatchImportDialog(self.app, [self.source, other])
        specs = [item[2] for item in dialog.items]
        reports = [(spec, precheck(spec)) for spec in specs]
        original = self.app.store.import_sheet
        def write(spec, *args, **kwargs):
            if spec.path == other:
                raise OSError('模拟磁盘写入失败')
            return original(spec, *args, **kwargs)
        with patch.object(self.app.store, 'import_sheet', side_effect=write), \
             patch('ledger_viewer.ui.messagebox.askyesno', return_value=True), \
             patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog._import_prechecked(reports)
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.selected, {1})
        self.assertIn('已完成', dialog.view.set('0', 'state'))
        self.assertIn('模拟磁盘写入失败', dialog.view.set('1', 'state'))
        with patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog._import_prechecked([reports[1]])
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(len(self.app.store.sources()), 2)

    def test_large_import_inventory_precheck_and_write_run_in_background(self):
        other = self.base / 'async.csv'
        other.write_text(self.source.read_text().replace('业务', '异步业务'), encoding='utf-8')
        with patch('ledger_viewer.ui.requires_background', return_value=True), \
             patch('ledger_viewer.ui.messagebox.showinfo'), \
             patch('ledger_viewer.ui.messagebox.askyesno', return_value=True):
            dialog = BatchImportDialog(self.app, [other])
            self.assertTrue(dialog.working)
            self.wait_for(lambda: not dialog.working)
            self.assertEqual(len(dialog.items), 1)
            dialog.use_ai.set(False)
            dialog._import_ready()
            self.assertTrue(dialog.working)
            self.wait_for(lambda: not dialog.winfo_exists())
        self.assertFalse(self.app.import_busy)
        self.assertEqual(len(self.app.store.sources()), 2)

    def test_large_single_import_preview_preserves_manual_mapping(self):
        with patch('ledger_viewer.ui.requires_background', return_value=True):
            saved = []
            dialog = ImportDialog(self.app, self.source, spec=self.spec, sheet_name='CSV', on_save=saved.append)
            self.assertTrue(dialog.tasks.busy('preview'))
            self.wait_for(lambda: not dialog.tasks.busy('preview'))
            self.assertEqual(dialog.vars['credit'].get(), dialog.columns[self.spec.mapping['credit']])
            dialog.import_now()
            self.assertEqual(saved[0].mapping, self.spec.mapping)

    def test_cancelled_replacement_rolls_back_and_changed_file_is_rejected(self):
        stop = threading.Event()
        self.source.write_text('日期,凭证号,科目编码,科目名称,借方金额,贷方金额\n' +
            ''.join(f'2026-01-01,{i},1002,银行存款,1,0\n' for i in range(2000)), encoding='utf-8')
        spec = classify_sheet(self.source, 'CSV')
        report = precheck(spec)
        with self.assertRaises(Cancelled):
            self.app.store.import_sheet(spec, replace=True, cancelled=stop,
                progress=lambda row: stop.set() if row >= 1000 else None)
        self.assertEqual(len(self.app.store.entries()), 2)
        self.assertEqual(len(self.app.store.sources()), 1)
        self.source.write_text(self.source.read_text() + '2026-01-01,3000,1002,银行存款,1,0\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '原文件发生变化'):
            self.app.store.import_sheet(spec, replace=True, expected_fingerprint=report.fingerprint)
        self.assertEqual(len(self.app.store.entries()), 2)

    def test_compact_layout_keeps_actions_visible_at_all_supported_fonts(self):
        self.app.tabs.select(1)
        self.app.geometry('820x520')
        for size in (9, 13, 17, 20):
            self.root.set_font_size(size, persist=False)
            self.root.update()
            frame = self.app.nametowidget(self.app.tabs.select())
            pending = [frame]
            buttons = []
            while pending:
                widget = pending.pop()
                if widget in (self.app.detail_tools.panel, self.app.detail_paging):
                    continue
                pending.extend(widget.winfo_children())
                if isinstance(widget, (ttk.Button, ttk.Menubutton)):
                    buttons.append(widget)
            for button in buttons:
                self.assertTrue(button.winfo_ismapped(), (size, button.cget('text')))
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth() - 8, (size, button.cget('text')))
            self.assertTrue(self.app.detail_entry.winfo_ismapped())
            self.assertFalse(self.app.detail_paging.winfo_ismapped())
        self.app.account_label.set("名称较长的明细科目，用于验证展开侧栏时的布局 · 含子科目")
        self.app.sidebar_toggle.invoke()
        self.root.update()
        for widget in (self.app.detail_mark, self.app.detail_entry, self.app.detail_more, *self.app.sidebar_buttons):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                 self.app.winfo_rootx() + self.app.winfo_width())
            self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight())

    def test_result_queries_share_context_and_count_membership_without_full_rows(self):
        batch = prepare_batch(self.app.store, {})
        with patch('ledger_viewer.jev_review.iter_entries', side_effect=AssertionError('full row scan')):
            context = voucher_result_context(self.app.store, batch)
            self.assertEqual(sum(context['totals'].values()), 2)
            voucher_results(self.app.store, batch, lane='all', context=context)
            voucher_results(self.app.store, batch, lane='candidates', context=context)
        for row in self.app.store.entries():
            self.assertIn(voucher_key(row), context['totals'])
        self.assertIs(voucher_totals(self.app.store), context['totals'])
        self.app.store.delete_source(self.app.store.sources()[0]['id'])
        self.assertEqual(voucher_totals(self.app.store), {})

    def test_hidden_review_page_does_not_rebuild_results_on_progress(self):
        panel = self.app.review_panel
        panel.batch_id = 1
        events = Queue()
        events.put(('progress', 1))
        job = SimpleNamespace(events=events, batch_id=1)
        panel.after_cancel(panel._poll_id)
        with patch('ledger_viewer.jev_ui.active_job', return_value=job), \
             patch.object(panel, 'refresh_rows') as rows, patch.object(panel, '_counts') as counts:
            panel._poll()
        rows.assert_not_called()
        counts.assert_not_called()

    def test_cancelled_preview_rolls_back_without_network(self):
        stop = threading.Event()
        stop.set()
        with patch('ledger_viewer.jev_review.TypeSafeClient') as client:
            with self.assertRaises(Cancelled):
                prepare_batch(self.app.store, {}, cancelled=stop)
        client.assert_not_called()
        self.assertEqual(self.app.store.db.execute('SELECT COUNT(*) FROM jev_batches').fetchone()[0], 0)

    def test_background_preview_cannot_open_consent_after_book_exit(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def prepare(store, scope, *, cancelled, progress):
            started.set()
            release.wait(3)
            finished.set()
            if cancelled.is_set():
                raise Cancelled()
            return 123
        with patch.object(self.app.store, 'large_book', return_value=True), \
             patch('ledger_viewer.jev_ui.prepare_batch', side_effect=prepare), \
             patch('ledger_viewer.jev_ui.ConsentDialog') as consent:
            self.app.review_panel.prepare({})
            self.wait_for(started.is_set)
            self.app.close_book()
            release.set()
            self.wait_for(finished.is_set)
            self.root.update()
            consent.assert_not_called()
        self.assertFalse(self.app.review_panel.preparing)

    def test_stopping_background_import_rolls_back_current_sheet(self):
        path = self.base / 'cancel.csv'
        path.write_text('日期,凭证号,科目编码,科目名称,借方金额,贷方金额\n' +
            ''.join(f'2026-01-01,{i},1002,银行存款,1,0\n' for i in range(2000)), encoding='utf-8')
        started, release = threading.Event(), threading.Event()
        original = LedgerStore.import_sheet
        def write(store, spec, *args, **kwargs):
            report = kwargs.get('progress', lambda count: None)
            def progress(count):
                report(count)
                started.set()
                release.wait(3)
            kwargs['progress'] = progress
            return original(store, spec, *args, **kwargs)
        with patch('ledger_viewer.ui.requires_background', return_value=True), \
             patch.object(LedgerStore, 'import_sheet', write), \
             patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog = BatchImportDialog(self.app, [path])
            self.wait_for(lambda: not dialog.working)
            dialog.use_ai.set(False)
            dialog._import_ready()
            self.wait_for(started.is_set)
            dialog._close()
            release.set()
            self.wait_for(lambda: not dialog.working)
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(len(self.app.store.sources()), 1)
        self.assertEqual(len(self.app.store.entries()), 2)
        self.assertIn('已停止', dialog.ai_status.get())

    def test_background_export_preserves_destination_on_failure_then_exports_all(self):
        target = self.base / 'export.csv'
        target.write_text('existing file', encoding='utf-8')
        def fail(store, path, **filters):
            path.write_text('partial output', encoding='utf-8')
            raise OSError('模拟导出失败')
        with patch.object(self.app.store, 'large_book', return_value=True), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename', return_value=str(target)), \
             patch('ledger_viewer.ui.messagebox.showerror') as error, \
             patch.object(LedgerStore, 'export_entries', fail):
            self.app.export_csv()
            self.wait_for(lambda: not self.app.tasks.busy('export'))
            error.assert_called_once()
        self.assertEqual(target.read_text(), 'existing file')
        with patch.object(self.app.store, 'large_book', return_value=True), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename', return_value=str(target)), \
             patch('ledger_viewer.ui.messagebox.showinfo'):
            self.app.export_csv()
            self.wait_for(lambda: not self.app.tasks.busy('export'))
        with target.open(encoding='utf-8-sig') as handle:
            self.assertEqual(len(list(csv.reader(handle))), 3)

    def test_ai_detail_note_is_protected_when_leaving_book(self):
        from ledger_viewer.jev_ui import ReviewDetail
        batch = prepare_batch(self.app.store, {})
        self.app.review_panel.batch_id = batch
        group = voucher_results(self.app.store, batch, lane='all')[0]
        dialog = ReviewDetail(self.app.review_panel, group)
        dialog._toggle_notes()
        dialog.note.insert('end', '补充凭证备注')
        with patch('ledger_viewer.jev_ui.messagebox.askyesnocancel', return_value=None):
            self.app.close_book()
        self.assertIsNotNone(self.app.store)
        self.assertTrue(dialog.winfo_exists())
        with patch('ledger_viewer.jev_ui.messagebox.askyesnocancel', return_value=True):
            self.assertTrue(dialog.request_close())
        self.assertEqual(list_marks(self.app.store)[0]['note'], '补充凭证备注')

    def test_large_source_removal_is_background_and_disables_conflicting_import(self):
        self.app.source_view.selection_set(str(self.app.store.sources()[0]['id']))
        with patch.object(self.app.store, 'large_book', return_value=True), \
             patch('ledger_viewer.ui.messagebox.askyesno', return_value=True), \
             patch('ledger_viewer.ui.filedialog.askopenfilenames') as choose:
            self.app.delete_selected_source()
            self.assertTrue(self.app.import_busy)
            self.app.import_file()
            choose.assert_not_called()
            self.wait_for(lambda: not self.app.import_busy)
        self.assertEqual(self.app.store.sources(), [])

    def test_progress_updates_are_coalesced_before_touching_tk(self):
        updates, completed = [], []
        def work(cancelled, report):
            for number in range(100):
                report(number)
            return 'done'
        self.app.tasks.submit('coalescing', work, completed.append, progress=updates.append)
        self.wait_for(lambda: bool(completed))
        self.assertEqual(updates, [99])
        self.assertEqual(completed, ['done'])

    def test_xlsx_merge_metadata_does_not_materialize_all_cells(self):
        from openpyxl import Workbook
        from ledger_viewer.importer import merged_ranges
        path = self.base / 'merged.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '中文 表'
        sheet['A1'] = '期末余额'
        sheet.merge_cells('A1:B1')
        sheet.merge_cells('C2:C4')
        workbook.save(path)
        workbook.close()
        with patch('ledger_viewer.importer.load_workbook', side_effect=AssertionError('full workbook load')):
            self.assertEqual(sorted(merged_ranges(path, '中文 表')), [(1, 1, 1, 2), (2, 4, 3, 3)])


if __name__ == '__main__':
    unittest.main()
