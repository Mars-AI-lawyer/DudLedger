import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ledger_viewer import issue_store
from ledger_viewer.importer import classify_sheet
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import list_marks, voucher_key
from ledger_viewer.voucher_summary import summarize, summaries_for_keys, voucher_lines_many


class VoucherBatchSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch('ledger_viewer.ui.Path.home', return_value=self.base):
            self.root = Application()
        self.root.set_font_size(13, persist=False)
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / '测试.sqlite')
        path = self.base / '测试.csv'
        path.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n' + ''.join(
            f'2026-01-02,{i},1002,银行存款,借方{i},{i}.01,0\n2026-01-02,{i},4001,实收资本,贷方{i},0,{i}.01\n' for i in range(1, 258)), encoding='utf-8')
        self.app.store.import_sheet(classify_sheet(path, 'CSV'))
        self.app.refresh_all()
        self.app.select_page('entries')
        self.root.update()
        self.selection = self.app.detail_selection
        self.rows = self.app.detail_view.get_children()
        self.key = self.app.detail_voucher_keys[self.rows[0]]
        self.other_key = self.app.detail_voucher_keys[self.rows[2]]

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def test_check_synchronizes_same_voucher_and_shift_range_deduplicates(self):
        view = self.app.detail_view
        box = view.bbox(self.rows[0], '#0')
        view.event_generate('<ButtonPress-1>', x=box[0] + 20, y=box[1] + 10)
        self.assertEqual(self.selection.checked, {self.key})
        self.assertTrue(view.item(self.rows[0], 'text').startswith('☑'))
        self.assertTrue(view.item(self.rows[1], 'text').startswith('☑'))
        box = view.bbox(self.rows[5], '#0')
        self.selection.press(SimpleNamespace(x=box[0] + 20, y=box[1] + 10, state=1))
        self.assertEqual(len(self.selection.checked), 3)
        self.assertFalse(hasattr(self.app, 'drag'))

    def test_checks_survive_pages_and_reset_on_scope_changes(self):
        self.selection.checked.add(self.key)
        self.selection.refresh()
        self.app.change_detail_page(1)
        self.root.update()
        self.assertIn(self.key, self.selection.checked)
        self.assertIn('跨页 1 张', self.selection.info.get())
        self.app.change_detail_page(-1)
        self.root.update()
        self.assertTrue(self.app.detail_view.item(self.rows[1], 'text').startswith('☑'))
        self.app.sort_details('debit')
        self.assertFalse(self.selection.checked)
        self.selection.checked.add(self.key)
        self.app.search.set('借方2')
        self.app.submit_detail_search()
        self.assertFalse(self.selection.checked)

    def test_rectangle_context_and_checks_share_explicit_priority(self):
        tools = self.app.detail_tools
        tools.select(self.rows[0], 'date')
        tools.select(self.rows[3], 'debit', extend=True)
        self.assertEqual(len(self.selection.action_keys()), 2)
        self.selection.checked.add(self.app.detail_voucher_keys[self.rows[6]])
        self.selection.refresh()
        self.assertEqual(len(self.selection.action_keys()), 1)
        self.app.mark_selected_entry()
        self.assertEqual(len(list_marks(self.app.store)), 1)
        self.assertFalse(self.app.voucher_windows)
        self.selection.clear()
        self.app.mark_selected_entry()
        self.assertEqual(len(list_marks(self.app.store)), 3)

    def test_disjoint_rectangles_mark_and_link_only_the_selected_vouchers(self):
        tools = self.app.detail_tools
        tools.select(self.rows[0], 'debit')
        tools.select(self.rows[4], 'credit', additive=True)
        tools.select(self.rows[5], 'debit', additive=True)
        expected = {self.key, self.app.detail_voucher_keys[self.rows[4]]}
        self.assertEqual(set(self.selection.action_keys()), expected)
        self.assertIn('（2）', self.app.detail_mark.cget('text'))
        self.app.mark_selected_entry()
        self.assertEqual({row['key'] for row in list_marks(self.app.store)}, expected)
        issue = issue_store.create_issue(self.app.store, '多区域问题')
        with patch.object(self.app.workbench, 'choose_issue') as choose:
            self.app.detail_join.invoke()
        self.assertEqual(set(choose.call_args.args[0]), expected)
        self.app.workbench.add_to_issue(issue, choose.call_args.args[0])
        self.assertEqual({row['voucher_key'] for row in issue_store.issue_vouchers(self.app.store, issue)}, expected)

    def test_mark_and_link_batches_roll_back_all_writes_on_late_failure(self):
        issue = issue_store.create_issue(self.app.store, '问题')
        with self.assertRaises(ValueError):
            issue_store.mark_vouchers(self.app.store, [self.key, 'invalid'])
        self.assertFalse(list_marks(self.app.store))
        with self.assertRaises(ValueError):
            issue_store.link_vouchers(self.app.store, issue, [self.key, 'invalid'])
        self.assertFalse(list_marks(self.app.store))
        self.assertEqual(issue_store.issue_vouchers(self.app.store, issue), [])
        self.assertEqual(self.app.store.db.execute('SELECT COUNT(*) FROM dd_voucher_refs').fetchone()[0], 0)

    def test_create_problem_and_links_roll_back_together(self):
        with self.assertRaises(ValueError):
            issue_store.create_issue_with_vouchers(self.app.store, '失败问题', [self.key, 'invalid'])
        self.assertEqual(issue_store.list_issues(self.app.store), [])
        self.assertFalse(list_marks(self.app.store))
        issue = issue_store.create_issue_with_vouchers(self.app.store, '成功问题', [self.key, self.other_key])
        self.assertEqual(len(issue_store.issue_vouchers(self.app.store, issue)), 2)

    def test_confirm_snapshots_is_atomic_when_one_voucher_disappears(self):
        issue = issue_store.create_issue_with_vouchers(self.app.store, '问题', [self.key, self.other_key])
        before = self.app.store.db.execute('SELECT signature FROM dd_voucher_refs WHERE voucher_key=?', (self.key,)).fetchone()[0]
        with self.app.store.db:
            self.app.store.db.execute("UPDATE entries SET summary='新摘要' WHERE voucher_no='1'")
            self.app.store.db.execute("DELETE FROM entries WHERE voucher_no='2'")
        with self.assertRaises(ValueError):
            issue_store.confirm_references(self.app.store, [self.key, self.other_key])
        after = self.app.store.db.execute('SELECT signature FROM dd_voucher_refs WHERE voucher_key=?', (self.key,)).fetchone()[0]
        self.assertEqual(before, after)

    def test_repeat_batch_does_not_reset_note_or_review_signature(self):
        issue_store.mark_vouchers(self.app.store, [self.key])
        with self.app.store.db:
            self.app.store.db.execute("UPDATE voucher_marks SET note='原备注',signature='old' WHERE voucher_key=?", (self.key,))
        result = issue_store.mark_vouchers(self.app.store, [self.key, self.key, self.other_key])
        self.assertEqual(result, {'added': 1, 'duplicates': 1})
        row = self.app.store.db.execute('SELECT * FROM voucher_marks WHERE voucher_key=?', (self.key,)).fetchone()
        self.assertEqual((row['note'], row['signature']), ('原备注', 'old'))

    def test_shared_summary_uses_single_side_and_chinese_names(self):
        lines = voucher_lines_many(self.app.store, [self.key])[self.key]
        summary = summarize(lines)
        self.assertEqual(summary['amount'], '1.01')
        self.assertEqual(summary['accounts'], '银行存款、实收资本')
        self.assertIn('借方1', summary['summary'])
        self.assertIn('贷方1', summary['summary'])
        lines[0]['debit_cents'] = -123
        summary = summarize(lines)
        self.assertEqual(summary['amount'], '借 -1.23／贷 1.01')
        self.assertEqual(summary['amount_status'], '已导入借贷不一致')
        self.assertEqual(summarize([])['amount'], '—')

    def test_batched_reader_handles_period_date_and_source_row_keys(self):
        with self.app.store.db:
            self.app.store.db.execute("UPDATE entries SET fiscal_year='',period='' WHERE voucher_no='2'")
            self.app.store.db.execute("UPDATE entries SET voucher_no='' WHERE voucher_no='3'")
        entries = self.app.store.entries(limit=10)
        keys = list(dict.fromkeys(voucher_key(row) for row in entries))
        found = voucher_lines_many(self.app.store, keys)
        for key in keys:
            self.assertTrue(found[key])
        self.assertEqual(sum(len(found[key]) for key in keys), len(entries))

    def test_invalid_keys_do_not_crash_the_batch_reader(self):
        found = voucher_lines_many(self.app.store, ['invalid', '[]', '{}', 'null', 'true', '"text"'])
        self.assertFalse(found)

    def test_batch_cancel_attention_keeps_issue_links(self):
        issue = issue_store.create_issue(self.app.store, '问题')
        issue_store.link_vouchers(self.app.store, issue, [self.key, self.other_key])
        self.app.refresh_marks()
        self.app.mark_selection.checked.update((self.key, self.other_key))
        self.app.mark_selection.refresh()
        with patch('ledger_viewer.ui.messagebox.askyesno', return_value=True):
            self.app.remove_selected_mark()
        self.assertFalse(list_marks(self.app.store))
        self.assertEqual(len(issue_store.issue_vouchers(self.app.store, issue)), 2)

    def test_missing_snapshot_and_live_changes_are_explicit(self):
        issue = issue_store.create_issue(self.app.store, '问题')
        issue_store.link_vouchers(self.app.store, issue, [self.key])
        with self.app.store.db:
            self.app.store.db.execute("UPDATE entries SET summary='内容改变' WHERE voucher_no='1'")
        self.assertIn('内容变化', summaries_for_keys(self.app.store, [self.key])[self.key]['status'])
        with self.app.store.db:
            self.app.store.db.execute("DELETE FROM entries WHERE voucher_no='1'")
        row = summaries_for_keys(self.app.store, [self.key])[self.key]
        self.assertIsNone(row['entry_id'])
        self.assertEqual(row['amount'], '1.01')
        self.assertIn('快照', row['status'])
