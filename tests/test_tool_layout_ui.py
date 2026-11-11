import tempfile
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from openpyxl import Workbook

from ledger_viewer.importer import classify_sheet
from ledger_viewer.ui import Application, SettingsDialog, visible_columns


class ToolLayoutUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        with patch('ledger_viewer.ui.Path.home', return_value=self.base):
            self.root = Application()
        self.app = next(iter(self.root.windows))
        self.addCleanup(self.app._close)
        self.app.geometry('1160x690')
        self.app._use_book(self.base / '界面.sqlite')
        path = self.base / '明细.csv'
        path.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                        '2026-01-02,1,1002,银行存款,股东出资,200,0\n', encoding='utf-8')
        self.app.store.import_sheet(classify_sheet(path, 'CSV'))
        self.app.refresh_all()
        self.app.select_page('entries')
        self.root.update()

    def drag_grip(self, shift):
        grip = self.app.workbench.grip
        grip.event_generate('<ButtonPress-1>', x=3, y=80)
        grip.event_generate('<B1-Motion>', x=3 + shift, y=80)
        grip.event_generate('<ButtonRelease-1>', x=3 + shift, y=80)
        self.root.update()

    def assert_fitted(self, view):
        self.assertEqual(view.xview(), (0.0, 1.0))
        self.assertLessEqual(sum(view.column(key, 'width') for key in self.app.table_fit.columns(view)), view.winfo_width() - 2)

    def test_main_columns_fit_both_tools_and_restore_after_closing(self):
        view = self.app.detail_view
        original = {key: view.column(key, 'width') for key in self.app.table_fit.columns(view)}
        height = view.winfo_height()
        self.app.workbench.show()
        self.root.update()
        self.assertFalse(self.app.workbench.detached)
        self.assert_fitted(view)
        self.assertEqual(view.winfo_height(), height)
        self.drag_grip(55)
        self.assert_fitted(view)
        self.app.workbench.hide()
        self.root.update()
        self.assertEqual({key: view.column(key, 'width') for key in original}, original)
        self.app.workbench.show_calculator()
        self.root.update()
        self.assert_fitted(view)
        self.app.workbench.hide()
        self.root.update()
        self.assertEqual({key: view.column(key, 'width') for key in original}, original)

    def test_drag_changes_each_tool_width_independently(self):
        panel = self.app.workbench
        panel.show()
        self.root.update()
        issue_width = panel.winfo_width()
        self.drag_grip(60)
        self.assertLess(panel.winfo_width(), issue_width - 40)
        saved_issue_width = panel.width
        panel.show_calculator()
        self.root.update()
        calc_width = panel.winfo_width()
        self.drag_grip(-55)
        self.assertGreater(panel.winfo_width(), calc_width + 40)
        saved_calc_width = panel.calculator_width
        panel.show()
        self.root.update()
        self.assertEqual(panel.winfo_width(), saved_issue_width)
        panel.show_calculator()
        self.root.update()
        self.assertEqual(panel.winfo_width(), saved_calc_width)

    def test_source_column_stays_hidden_for_saved_vouchers_and_imported_reports(self):
        self.assertEqual(visible_columns('voucher', ['code', 'debit', 'source']), ['code', 'debit'])
        self.app.store.save_display_columns('voucher', ['code', 'debit', 'source'])
        self.app.open_voucher(None, entry_id=self.app.store.entries()[0]['id'])
        dialog = next(iter(self.app.voucher_windows))
        self.assertNotIn('source', dialog.cell_tools.columns())
        book = Workbook()
        sheet = book.active
        sheet.title = '资产负债表'
        sheet.append(['资产', '期末数', '年初数', '负债与权益', '期末数', '年初数'])
        sheet.append(['银行存款', 200, 0, '实收资本', 200, 0])
        path = self.base / '报表.xlsx'
        book.save(path)
        self.app.store.import_sheet(classify_sheet(path, sheet.title))
        self.app.refresh_all()
        view = self.app.report_views['balance_sheet']
        self.assertNotIn('source', view.cget('displaycolumns'))
        self.assertTrue(view.set(view.get_children()[0], 'source'))

    def test_storage_help_is_folded_and_sampling_uses_tick_indicator(self):
        with patch('ledger_viewer.ui.get_api_key', return_value=''), patch('ledger_viewer.ui.deepseek_get_api_key', return_value=''), patch('ledger_viewer.ui.bailian_get_api_key', return_value=''):
            dialog = SettingsDialog(self.app)
        self.assertFalse(dialog.storage_help.winfo_manager())
        dialog.storage_toggle.invoke()
        self.assertEqual(dialog.storage_help.winfo_manager(), 'pack')
        self.assertIn('.sqlite-wal', dialog.storage_help.cget('text'))
        self.assertIn('.sqlite-shm', dialog.storage_help.cget('text'))
        dialog.storage_toggle.invoke()
        self.assertFalse(dialog.storage_help.winfo_manager())
        dialog.destroy()
        panel = self.app.review_panel
        self.app.select_page('review')
        self.root.update()
        selection = next(child for child in panel.winfo_children() if isinstance(child, ttk.LabelFrame))
        checkbox = selection.winfo_children()[0]
        name = checkbox.cget('text')
        self.assertEqual(checkbox.cget('style'), 'Sampling.TCheckbutton')
        checkbox.invoke()
        self.assertTrue(panel.review_accounts[name].get())
        with patch.object(panel, 'prepare') as prepare:
            panel.new_batch()
        prepare.assert_called_once_with({'review_accounts': [name]})


if __name__ == '__main__':
    unittest.main()
