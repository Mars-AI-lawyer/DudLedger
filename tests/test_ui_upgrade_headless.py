"""Headless regression checks for the UI/UX implementation, without Tk windows."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer.importer import classify_sheet
from ledger_viewer.store import LedgerStore
from ledger_viewer.table_tools import TableTools
from ledger_viewer.ui import App, RelatedPartiesDialog, RelatedPartiesPanel, SettingsDialog, visible_columns
from ledger_viewer.voucher_selection import VoucherSelection


class Variable:
    def __init__(self, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class RelatedPartiesDialogHeadlessTest(unittest.TestCase):
    def test_dialog_centers_before_showing_and_focuses_name(self):
        dialog, parent, window = RelatedPartiesDialog.__new__(RelatedPartiesDialog), Mock(), Mock()
        dialog._w = '.parties'
        for method in ('withdraw', 'destroy', 'title', 'transient', 'bind', 'minsize', 'geometry', 'deiconify', 'grab_set'):
            setattr(dialog, method, getattr(window, method))
        parent.winfo_x.return_value, parent.winfo_y.return_value = 80, 90
        parent.winfo_width.return_value, parent.winfo_height.return_value = 1100, 700
        parent.winfo_vrootx.return_value = parent.winfo_vrooty.return_value = 0
        parent.winfo_vrootwidth.return_value, parent.winfo_vrootheight.return_value = 1600, 1000
        with patch('ledger_viewer.ui.tk.Toplevel.__init__', return_value=None), \
             patch('ledger_viewer.ui.RelatedPartiesPanel') as panel:
            RelatedPartiesDialog.__init__(dialog, parent)
        panel.assert_called_once_with(dialog, parent, on_close=dialog.destroy)
        dialog.geometry.assert_called_once_with('700x520+280+180')
        dialog.panel.name_entry.focus_set.assert_called_once_with()
        method_names = [entry[0] for entry in window.mock_calls]
        self.assertLess(method_names.index('geometry'), method_names.index('deiconify'))

    def test_new_party_action_clears_selection_and_focuses_name(self):
        panel = SimpleNamespace(view=Mock(), name_var=Variable('甲方'), relation_var=Variable('股东'), name_entry=Mock())
        panel.view.selection.return_value = ('1',)
        RelatedPartiesPanel._clear(panel)
        panel.view.selection_remove.assert_called_once_with('1')
        panel.name_entry.focus_set.assert_called_once_with()
        self.assertEqual((panel.name_var.get(), panel.relation_var.get()), ('', ''))


class LedgerSearchAndNavigationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        source = base / 'fictional.csv'
        source.write_text(
            '日期,会计年度,期间,凭证字,凭证号,科目编码,科目名称,摘要,往来单位,辅助核算,借方金额,贷方金额\n'
            '2026-01-01,2026,1,记,1,1002,银行存款,首期款,甲公司,项目A,100,0\n'
            '2026-01-01,2026,1,记,1,2202,应付账款,首期款,甲公司,项目A,0,100\n'
            '2026-01-02,2026,1,记,2,6001,主营业务收入,服务费,乙公司,项目B,0,80\n'
            '2026-01-02,2026,1,记,2,1002,银行存款,服务费,乙公司,项目B,80,0\n'
            '2026-01-03,2026,1,记,88,1002,银行存款,尾款,丙公司,项目C,20,0\n'
            '2026-01-03,2026,1,记,88,2202,应付账款,尾款,丙公司,项目C,0,20\n', encoding='utf-8')
        self.store = LedgerStore(base / 'book.sqlite')
        self.store.import_sheet(classify_sheet(source, 'CSV'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_general_search_covers_all_six_fields(self):
        for term, expected in [('项目B', 2), ('乙公司', 2), ('88', 2), ('6001', 1), ('主营业务收入', 1), ('服务费', 2)]:
            with self.subTest(term=term):
                rows, total, _ = self.store.entries_page(search=term)
                self.assertEqual(total, expected)
                self.assertEqual(len(rows), expected)

    def test_voucher_navigation_deduplicates_lines_and_honours_filters(self):
        first = self.store.entries(search='项目A')[0]
        context = self.store.voucher_navigation(first['id'])
        self.assertEqual((context['position'], context['total']), (1, 3))
        self.assertIsNone(context['previous_entry_id'])
        second = self.store.entry(context['next_entry_id'])
        self.assertEqual(second['voucher_no'], '2')
        middle = self.store.voucher_navigation(second['id'], sort_by='date', descending=False)
        self.assertEqual((middle['position'], middle['total']), (2, 3))
        self.assertEqual(self.store.entry(middle['previous_entry_id'])['voucher_no'], '1')
        self.assertEqual(self.store.entry(middle['next_entry_id'])['voucher_no'], '88')
        filtered = self.store.voucher_navigation(second['id'], search='乙公司')
        self.assertEqual((filtered['position'], filtered['total']), (1, 1))
        self.assertIsNone(filtered['previous_entry_id'])
        self.assertIsNone(filtered['next_entry_id'])

    def test_navigation_cache_reuses_scope_and_invalidates_on_data_change(self):
        entry_id = self.store.entries()[0]['id']
        self.store.voucher_navigation(entry_id)
        cache = self.store._voucher_navigation_cache
        self.store.voucher_navigation(entry_id)
        self.assertIs(self.store._voucher_navigation_cache, cache)
        self.store.voucher_navigation(entry_id, search='项目A')
        self.assertIsNot(self.store._voucher_navigation_cache, cache)
        scoped = self.store._voucher_navigation_cache
        with self.store.db:
            self.store.db.execute('UPDATE entries SET summary=summary WHERE id=?', (entry_id,))
        self.store.voucher_navigation(entry_id, search='项目A')
        self.assertIsNot(self.store._voucher_navigation_cache, scoped)

    def test_voucher_source_column_can_be_enabled(self):
        self.assertEqual(visible_columns('voucher', ['code', 'debit', 'source']), ['code', 'debit', 'source'])


class SelectionAndSettingsTest(unittest.TestCase):
    def test_checked_and_cell_ranges_have_distinct_persistent_copy(self):
        selection = VoucherSelection.__new__(VoucherSelection)
        selection.checked = {'a', 'b', 'c'}
        selection.view = Mock()
        selection.view.get_children.return_value = ('1', '2')
        selection.key_for_row = lambda row: {'1': 'a', '2': 'b'}[row]
        selection.info = Variable()
        selection.display_info = Variable()
        selection.context = '共 20 条'
        selection.feedback = ''
        selection.on_change = None
        selection.changed()
        self.assertIn('含跨页 1 张', selection.display_info.get())
        self.assertIn('后续操作使用勾选范围', selection.display_info.get())
        selection.checked.clear()
        selection.view.selection.return_value = ('1',)
        selection.view.cell_tools = None
        selection.changed()
        self.assertIn('尚未勾选', selection.display_info.get())

    def test_detail_enter_ignores_pointer_coordinates_but_click_guard_remains(self):
        app = SimpleNamespace(store=Mock(), detail_view=Mock())
        app.detail_view.selection.return_value = ('42',)
        app.store.voucher_lines.return_value = []
        keyboard = SimpleNamespace(keysym='Return', x=2, y=2)
        self.assertIsNone(App.open_voucher(app, keyboard))
        app.store.voucher_lines.assert_called_once_with(42)
        app.detail_view.identify_column.assert_not_called()
        app.store.voucher_lines.reset_mock()
        mouse = SimpleNamespace(keysym=None, x=2, y=2)
        app.detail_view.identify_region.return_value = 'heading'
        self.assertIsNone(App.open_voucher(app, mouse))
        app.detail_view.identify_region.return_value = 'tree'
        app.detail_view.identify_column.return_value = '#0'
        self.assertEqual(App.open_voucher(app, mouse), 'break')
        app.store.voucher_lines.assert_not_called()

    def test_mark_enter_uses_selection_but_double_click_uses_pointer_row(self):
        app = SimpleNamespace(mark_keys={'selected': 'key-selected', 'pointed': 'key-pointed'},
                              mark_view=Mock(), workbench=Mock())
        app.mark_view.selection.return_value = ('selected',)
        keyboard = SimpleNamespace(keysym='Return', x=2, y=2)
        App.open_selected_mark_voucher(app, keyboard)
        app.workbench.open_key.assert_called_once_with('key-selected')
        app.mark_view.identify_row.assert_not_called()
        app.mark_view.identify_column.assert_not_called()

        app.workbench.open_key.reset_mock()
        app.mark_view.identify_column.return_value = 'summary'
        app.mark_view.identify_row.return_value = 'pointed'
        mouse = SimpleNamespace(keysym=None, x=30, y=30)
        App.open_selected_mark_voucher(app, mouse)
        app.workbench.open_key.assert_called_once_with('key-pointed')
        app.mark_view.identify_column.return_value = '#0'
        self.assertEqual(App.open_selected_mark_voucher(app, mouse), 'break')

    def test_keyboard_navigation_and_escape_respect_cell_selection(self):
        tools = TableTools.__new__(TableTools)
        tools.active = None
        tools.saved_ranges = []
        self.assertIsNone(tools._move('Down'))
        self.assertIsNone(tools._escape())
        tools.active = ('1', 'debit')
        tools.reset_selection = Mock()
        self.assertEqual(tools._escape(), 'break')
        tools.reset_selection.assert_called_once()

    def test_close_without_changes_discard_and_cancel_have_distinct_results(self):
        for dirty, answer, closes in ((False, None, True), (True, False, True), (True, None, False)):
            dialog = SettingsDialog.__new__(SettingsDialog)
            dialog._stop_shortcut_recording = Mock()
            dialog._refresh_dirty = Mock()
            dialog.dirty = dirty
            dialog.destroy = Mock()
            dialog._finish = Mock()
            with patch('ledger_viewer.ui.messagebox.askyesnocancel', return_value=answer) as ask:
                result = SettingsDialog._request_close(dialog)
            self.assertEqual(result, closes)
            if dirty:
                ask.assert_called_once()
                dialog._finish.assert_not_called()
            else:
                ask.assert_not_called()
            self.assertEqual(dialog.destroy.called, closes)

    def test_clearing_report_selection_restores_idle_status(self):
        app = SimpleNamespace(status=Variable('旧合计'), report_kind=Variable('balance_sheet'))
        app.current_page = lambda: 'reports'
        tools = SimpleNamespace(stats=Variable('合计 100.00'))
        App._table_stats_feedback(app, tools, 'reports', 'balance_sheet')
        self.assertEqual(app.status.get(), '合计 100.00')
        tools.stats.set('')
        App._table_stats_feedback(app, tools, 'reports', 'balance_sheet')
        self.assertEqual(app.status.get(), '资产负债表 · 框选金额可复制或计算')
        tools.stats.set('其他报表旧合计')
        App._table_stats_feedback(app, tools, 'reports', 'income_statement')
        self.assertEqual(app.status.get(), '资产负债表 · 框选金额可复制或计算')

    def test_book_close_honours_settings_cancellation_and_closes_stale_panels(self):
        app = SimpleNamespace(settings_window=Mock(), related_parties_window=Mock())
        app.settings_window.winfo_exists.return_value = True
        app.related_parties_window.winfo_exists.return_value = True
        app.settings_window._request_close.return_value = False
        self.assertFalse(App._close_book_dialogs(app))
        app.related_parties_window.destroy.assert_not_called()
        app.settings_window._request_close.return_value = True
        self.assertTrue(App._close_book_dialogs(app))
        app.related_parties_window.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
