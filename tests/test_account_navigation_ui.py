import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.detail_filters import EntryCondition
from ledger_viewer.importer import classify_sheet
from ledger_viewer.ui import Application


class AccountNavigationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch('ledger_viewer.ui.Path.home', return_value=self.base):
            self.root = Application()
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / '测试.sqlite')
        source = self.base / '科目.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                          '2026-01-01,1,1002,银行存款,款项,1,0\n'
                          '2026-01-02,2,100201,银行存款人民币,款项,2,0\n'
                          '2026-01-03,3,2241,其他应付款,款项,0,3\n', encoding='utf-8')
        self.app.store.import_sheet(classify_sheet(source, 'CSV'))
        self.app.refresh_all()
        self.app.select_page('entries')
        self.app.toggle_detail_accounts(True)
        self.root.update()
        self.nav = self.app.detail_accounts

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def test_search_keeps_parent_and_switch_preserves_detail_filters(self):
        self.app.search.set('款项')
        self.app.submit_detail_search()
        self.app.sort_details('debit', True)
        self.app._set_detail_filter('debit', EntryCondition('debit', 'ge', ('1',)))
        self.nav.search.set('人民币')
        self.root.update()
        self.assertEqual(self.nav.tree.parent('C:100201'), 'C:1002')
        self.assertFalse(self.nav.tree.exists('C:2241'))
        self.nav.tree.selection_set('C:1002')
        self.root.update()
        self.assertEqual(self.app.account, 'C:1002')
        self.assertTrue(self.app.include_descendants)
        self.assertEqual(self.app.detail_total, 2)
        self.assertEqual(self.app.detail_conditions['summary'].values, ('款项',))
        self.assertIn('debit', self.app.detail_conditions)
        self.assertEqual((self.app.detail_sort, self.app.detail_descending), ('debit', True))
        self.nav.tree.selection_set('C:100201')
        self.root.update()
        self.assertEqual(self.app.detail_total, 1)
        self.assertEqual(self.app.current_page(), 'entries')

    def test_collapsing_reopening_and_resetting_book(self):
        self.assertTrue(self.nav.winfo_ismapped())
        self.app.toggle_detail_accounts()
        self.root.update()
        self.assertFalse(self.nav.winfo_ismapped())
        self.assertEqual(self.app.store.setting('detail_account_tree'), 'hide')
        self.app.toggle_detail_accounts()
        self.root.update()
        self.assertTrue(self.nav.winfo_ismapped())
        self.nav.search.set('不存在')
        self.assertEqual(self.nav.tree.get_children(), ('all',))
        self.app.close_book()
        self.assertFalse(self.nav.accounts)
        self.assertEqual(self.nav.search.get(), '')

    def test_balance_search_does_not_limit_navigation_and_external_scope_syncs(self):
        self.app.summary_search.set('其他应付款')
        self.app.refresh_summary()
        self.app._refresh_detail_accounts()
        self.assertTrue(self.nav.tree.exists('C:100201'))
        self.app.summary_view.selection_set('C:2241')
        self.app.open_account(None)
        self.root.update()
        self.assertEqual(self.nav.tree.selection(), ('C:2241',))
        self.nav.tree.selection_set('all')
        self.root.update()
        self.assertIsNone(self.app.account)
        self.assertEqual(self.app.detail_total, 3)

    def test_visibility_is_restored_when_reopening_the_book(self):
        book = self.app.store.path
        self.app.toggle_detail_accounts(False)
        self.app.close_book()
        self.app._use_book(book)
        self.app.select_page('entries')
        self.root.update()
        self.assertFalse(self.nav.winfo_ismapped())
        self.assertEqual(len(self.nav.accounts), 3)

    def test_open_tree_keeps_batch_buttons_usable_with_large_fonts(self):
        self.app.geometry('820x520')
        for size in (13, 17, 20):
            self.root.set_font_size(size, persist=False)
            self.root.update()
            self.assertTrue(self.nav.winfo_ismapped())
            pending = [self.app.detail_table]
            while pending:
                widget = pending.pop()
                if widget is self.app.detail_tools.panel:
                    continue
                pending.extend(widget.winfo_children())
                if widget.winfo_class() == 'TButton':
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth() - 8)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         self.app.winfo_rootx() + self.app.winfo_width())

    def test_switching_accounts_clears_previous_voucher_checks(self):
        self.app.detail_selection.toggle_all()
        self.assertTrue(self.app.detail_selection.checked)
        self.nav.tree.selection_set('C:2241')
        self.root.update()
        self.assertFalse(self.app.detail_selection.checked)
        self.assertEqual(self.app.detail_total, 1)
