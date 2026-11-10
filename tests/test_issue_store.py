import tempfile
import unittest
from pathlib import Path

from ledger_viewer import issue_store as issues
from ledger_viewer.importer import classify_sheet
from ledger_viewer.store import LedgerStore
from ledger_viewer.voucher_notes import list_marks, remove_mark, save_mark, update_mark_note, voucher_key


class IssueStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / '账套.sqlite'
        self.store = LedgerStore(self.path)
        source = self.base / '账簿.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                          '2026-01-02,1,1002,银行存款,股东出资,200,0\n'
                          '2026-01-02,1,4001,实收资本,股东出资,0,200\n'
                          '2026-01-03,2,2241,其他应付款,往来款,100,0\n', encoding='utf-8')
        self.store.import_sheet(classify_sheet(source, 'CSV'))
        self.entry = self.store.entries()[0]
        self.key = voucher_key(self.entry)
        self.issue = issues.create_issue(self.store, '出资问题')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_many_to_many_deduplicates_and_keeps_existing_mark(self):
        save_mark(self.store, self.entry['id'], '原有备注')
        signature = self.store.db.execute('SELECT signature FROM voucher_marks').fetchone()[0]
        other = issues.create_issue(self.store, '关联往来')
        self.assertTrue(issues.link_voucher(self.store, self.issue, self.key))
        self.assertFalse(issues.link_voucher(self.store, self.issue, self.key))
        self.assertTrue(issues.link_voucher(self.store, other, self.key))
        self.assertEqual([row['count'] for row in issues.list_issues(self.store)], [1, 1])
        self.assertEqual(list_marks(self.store)[0]['note'], '原有备注')
        self.assertEqual(self.store.db.execute('SELECT signature FROM voucher_marks').fetchone()[0], signature)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dd_voucher_refs').fetchone()[0], 1)
        self.assertEqual(len(issues.voucher_preview(self.store, self.key)['lines']), 2)

    def test_preview_has_no_database_side_effects(self):
        before = self.store.db.total_changes
        self.assertEqual(issues.voucher_preview(self.store, self.key)['status'], '正常')
        self.assertEqual(self.store.db.total_changes, before)
        self.assertEqual(list_marks(self.store), [])
        self.assertEqual(issues.issue_vouchers(self.store, self.issue), [])

    def test_unmark_unlink_delete_are_independent(self):
        issues.link_voucher(self.store, self.issue, self.key)
        remove_mark(self.store, self.key)
        self.assertEqual(len(issues.issue_vouchers(self.store, self.issue)), 1)
        self.assertEqual(len(issues.voucher_preview(self.store, self.key)['lines']), 2)
        issues.link_voucher(self.store, self.issue, self.key)
        issues.unlink_voucher(self.store, self.issue, self.key)
        self.assertEqual(len(list_marks(self.store)), 1)
        issues.link_voucher(self.store, self.issue, self.key)
        issues.delete_issue(self.store, self.issue)
        self.assertEqual(issues.list_issues(self.store), [])
        self.assertEqual(len(list_marks(self.store)), 1)

    def test_changed_missing_and_returned_voucher_preserve_baseline(self):
        issues.link_voucher(self.store, self.issue, self.key)
        with self.store.db:
            self.store.db.execute("UPDATE entries SET summary='更改摘要'")
        preview = issues.voucher_preview(self.store, self.key)
        self.assertIn('内容变化', preview['status'])
        self.assertEqual(preview['snapshot'][0]['summary'], '股东出资')
        issues.write_note(self.store, self.key, '', '修改笔记不确认凭证')
        self.assertIn('内容变化', issues.voucher_preview(self.store, self.key)['status'])
        with self.store.db:
            self.store.db.execute('DELETE FROM entries')
        missing = issues.voucher_preview(self.store, self.key)
        self.assertIsNone(missing['entry_id'])
        self.assertIn('原凭证未匹配', missing['status'])
        self.assertEqual(len(missing['lines']), 2)
        other = issues.create_issue(self.store, '待补材料')
        self.assertTrue(issues.link_voucher(self.store, other, self.key))
        with self.assertRaises(ValueError):
            issues.confirm_reference(self.store, self.key)

    def test_confirm_snapshot_is_explicit(self):
        issues.link_voucher(self.store, self.issue, self.key)
        with self.store.db:
            self.store.db.execute("UPDATE entries SET summary='新内容'")
        issues.confirm_reference(self.store, self.key)
        self.assertEqual(issues.voucher_preview(self.store, self.key)['status'], '正常')
        # The separate mark review has not been acknowledged.
        self.assertEqual(list_marks(self.store)[0]['status'], '内容变化')

    def test_issue_edit_validation_and_optimistic_lock(self):
        issues.create_issue(self.store, '借款问题')
        with self.assertRaises(ValueError):
            issues.save_issue(self.store, self.issue, 0, '借款问题', '待核查', '', '')
        revision = issues.save_issue(self.store, self.issue, 0, '出资问题', '待补资料', '到账记录', '核对银行流水')
        self.assertEqual(revision, 1)
        with self.assertRaises(ValueError):
            issues.save_issue(self.store, self.issue, 0, '过时窗口', '待核查', '', '')
        self.assertEqual(issues.get_issue(self.store, self.issue)['notes'], '核对银行流水')

    def test_note_compare_and_set_preserves_other_edits_and_signature(self):
        issues.link_voucher(self.store, self.issue, self.key)
        update_mark_note(self.store, self.key, '另一个入口的备注')
        with self.assertRaises(ValueError):
            issues.write_note(self.store, self.key, '', '旧窗口输入')
        self.assertEqual(list_marks(self.store)[0]['note'], '另一个入口的备注')
        remove_mark(self.store, self.key)
        with self.assertRaises(ValueError):
            issues.write_note(self.store, self.key, '另一个入口的备注', '旧窗口不应重新关注')

    def test_link_transaction_rolls_back_snapshot_if_mark_save_fails(self):
        self.store.db.execute("CREATE TRIGGER fail_mark BEFORE INSERT ON voucher_marks BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaises(Exception):
            issues.link_voucher(self.store, self.issue, self.key)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dd_voucher_refs').fetchone()[0], 0)
        self.assertEqual(issues.issue_vouchers(self.store, self.issue), [])

    def test_reopen_and_legacy_additive_migration(self):
        issues.link_voucher(self.store, self.issue, self.key)
        issues.save_issue(self.store, self.issue, 0, '出资问题', '待补资料', '描述', '笔记')
        self.store.close()
        self.store = LedgerStore(self.path)
        self.assertEqual(issues.get_issue(self.store, self.issue)['notes'], '笔记')
        self.assertEqual(len(issues.issue_vouchers(self.store, self.issue)), 1)
        with self.store.db:
            self.store.db.execute('DROP TABLE dd_issue_vouchers')
            self.store.db.execute('DROP TABLE dd_voucher_refs')
            self.store.db.execute('DROP TABLE dd_issues')
        self.store.close()
        self.store = LedgerStore(self.path)
        self.assertEqual(issues.list_issues(self.store), [])
        self.assertEqual(len(list_marks(self.store)), 1)
