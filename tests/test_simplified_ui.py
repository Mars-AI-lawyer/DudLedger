import csv
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from ledger_viewer.importer import ImportSpec, discover_sheets, guess_header
from ledger_viewer.ui import Application, BatchImportDialog, ReportAccountsDialog


class SimplifiedUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = Application()
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / 'book.sqlite')
        self.store = self.app.store
        path = self.base / 'entries.csv'
        path.write_text('日期,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                        '2026-08-01,1002,银行存款,收款,2,0\n'
                        '2026-08-02,100201,基本户,收款,100,0\n'
                        '2026-08-03,2202,应付账款,采购,0,30\n'
                        '2026-08-04,6001,主营业务收入,销售,0,72\n', encoding='utf-8')
        header, _, mapping = guess_header(path, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(path, 'CSV', header, 'entry', mapping))
        report = self.base / 'reports.xlsx'
        book = Workbook()
        balance = book.active
        balance.title = '资产负债表'
        balance.append(['资产', '期末余额', '负债和所有者权益', '期末余额'])
        balance.append(['银行存款', 102, '应付账款', 30])
        balance.append(['货币资金', 102, None, None])
        balance.append(['无形资产', 0, None, None])
        income = book.create_sheet('利润表')
        income.append(['项目名称', '本期金额'])
        income.append(['一、主营业务收入', 72])
        income.append(['营业收入', 72])
        book.save(report)
        for spec in discover_sheets([report])[0]:
            self.store.import_sheet(spec)
        self.app.refresh_all()
        self.root.update()

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def click_report(self, kind, row, field):
        self.app.select_page('reports')
        self.app.report_kind.set(kind)
        self.app.switch_report()
        self.root.update()
        view = self.app.report_views[kind]
        if field == 'right_item':
            view.xview_moveto(.5)
            self.root.update()
        x, y, width, height = view.bbox(str(row), field)
        view.event_generate('<ButtonPress-1>', x=max(x + 5, 5), y=y + height // 2)
        view.event_generate('<ButtonRelease-1>', x=max(x + 5, 5), y=y + height // 2)
        self.root.update()

    def shown_codes(self):
        return [self.app.detail_view.set(item, 'code') for item in self.app.detail_view.get_children()]

    def test_report_clicks_preserve_detail_filters_and_never_navigate(self):
        self.app.search.set('不存在')
        self.app.period_from.set('2026年7期')
        self.app.period_to.set('2026年8期')
        for kind, field in (('balance_sheet', 'left_item'), ('balance_sheet', 'right_item'), ('income_statement', 'left_item')):
            self.click_report(kind, 2, field)
            self.assertEqual(self.app.current_page(), 'reports')
            self.assertEqual(self.app.search.get(), '不存在')
            self.assertEqual(self.app.period_from.get(), '2026年7期')
        self.assertFalse(any(isinstance(w, ReportAccountsDialog) for w in self.app.winfo_children()))

    def test_report_ranges_copy_sum_and_calculators_remain_independent(self):
        self.app.select_page('reports')
        self.root.update()
        tools = self.app.report_tools['balance_sheet']
        view = tools.view
        rows = view.get_children()
        tools.select(rows[0], 'left_a')
        tools.select(rows[1], 'left_a', extend=True)
        self.assertEqual(str(tools.summary.total), '204.00')
        with patch.object(view, 'clipboard_clear'), patch.object(view, 'clipboard_append') as copied:
            tools.copy_cell()
            self.assertEqual(copied.call_args.args[0], '102.00\n102.00')
        tools.calculate(1)
        self.app.report_kind.set('income_statement')
        self.app.switch_report()
        other = self.app.report_tools['income_statement']
        other.select(other.view.get_children()[1], 'left_a')
        other.calculate(-1)
        self.assertEqual(other.result.get(), '-72.00')
        self.assertEqual(tools.result.get(), '204.00')
        self.app.calculator_button.invoke()
        self.root.update()
        self.assertIn('利润表', self.app.workbench.calc_source.get())
        self.app.report_kind.set('balance_sheet')
        self.app.switch_report()
        self.assertIn('资产负债表', self.app.workbench.calc_source.get())

    def test_numeric_sort_filters_and_export_match(self):
        self.app.account = ['C:1002', 'C:100201']
        self.app.include_descendants = False
        self.app.refresh_details()
        self.app.select_page('entries')
        self.app.sort_details('debit')
        self.assertEqual(self.shown_codes(), ['1002', '100201'])
        self.app.sort_details('debit')
        self.assertEqual(self.shown_codes(), ['100201', '1002'])
        first = self.store.entries(account=['C:1002'], include_descendants=True, sort_by='debit', descending=True, limit=1)
        self.assertEqual(first[0]['debit_cents'], 10000)
        self.assertEqual(self.store.entries(account=[]), [])
        target = self.base / 'export.csv'
        with patch('ledger_viewer.ui.filedialog.asksaveasfilename', return_value=str(target)), patch('ledger_viewer.ui.messagebox.showinfo'):
            self.app.export_csv()
        with target.open(encoding='utf-8-sig') as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual([row['科目编码'] for row in exported], ['100201', '1002'])
        self.app.search.set('采购')
        self.app.submit_detail_search()
        self.assertEqual(self.shown_codes(), [])
        self.app.clear_filter()
        self.assertEqual(self.shown_codes(), ['100201', '1002'])
        self.app.geometry('820x600')
        self.root.update()
        for widget in self.app.detail_controls.winfo_children():
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.app.winfo_rootx() + self.app.winfo_width())

    def test_detail_period_range_spans_years(self):
        path = self.base / '跨年.csv'
        path.write_text('日期,会计年度,期间,凭证字,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                        '2023-01-15,2023,1,记,1,1002,银行存款,收款,10,0\n'
                        '2023-12-31,2023,12,记,2,1002,银行存款,收款,20,0\n'
                        '2024-01-05,2024,1,记,3,1002,银行存款,收款,30,0\n'
                        '2024-02-10,2024,2,记,4,1002,银行存款,收款,40,0\n'
                        '2024-03-20,2024,3,记,5,1002,银行存款,收款,50,0\n', encoding='utf-8')
        header, _, mapping = guess_header(path, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(path, 'CSV', header, 'entry', mapping))
        self.app.refresh_all()
        self.root.update()
        shown_dates = lambda: [self.app.detail_view.set(item, 'date') for item in self.app.detail_view.get_children()]
        # 2023年1期 至 2024年2期：闭区间跨年，含起止两期。
        self.app.period_from.set('2023年1期')
        self.app.period_to.set('2024年2期')
        self.app.refresh_details()
        self.assertEqual(shown_dates(), ['2023-01-15', '2023-12-31', '2024-01-05', '2024-02-10'])
        self.assertIn('2023年1期 至 2024年2期', self.app.detail_info.get())
        # 起止填反时自动对调，结果一致。
        self.app.period_from.set('2024年2期')
        self.app.period_to.set('2023年1期')
        self.app.refresh_details()
        self.assertEqual(len(shown_dates()), 4)
        # 只填起始期：该期及以后（含 setUp 中 2026年8期 的 4 行）。
        self.app.period_from.set('2024年2期')
        self.app.period_to.set('不限')
        self.app.refresh_details()
        self.assertEqual(shown_dates(), ['2024-02-10', '2024-03-20', '2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04'])
        # 重置筛选回到全部期间（setUp 4 行 + 本测试 5 行）。
        self.app.clear_filter()
        self.app.refresh_details()
        self.assertEqual(len(shown_dates()), 9)
        # 导出 CSV 与界面同一区间口径。
        self.app.period_from.set('2023年1期')
        self.app.period_to.set('2024年2期')
        self.app.refresh_details()
        target = self.base / 'range-export.csv'
        with patch('ledger_viewer.ui.filedialog.asksaveasfilename', return_value=str(target)), patch('ledger_viewer.ui.messagebox.showinfo'):
            self.app.export_csv()
        with target.open(encoding='utf-8-sig') as handle:
            exported = [row['日期'] for row in csv.DictReader(handle)]
        self.assertEqual(exported, ['2023-01-15', '2023-12-31', '2024-01-05', '2024-02-10'])

    def test_cashflow_is_absent_from_mixed_import_and_silent_when_alone(self):
        path = self.base / '混合报表.xlsx'
        book = Workbook()
        cash = book.active
        cash.title = '现金流量表'
        cash.append(['项目名称', '本期金额'])
        cash.append(['销售收到现金', 20])
        unnamed = book.create_sheet('Sheet2')
        unnamed.append(['现金流量表'])
        unnamed.append(['项目名称', '本期金额'])
        unnamed.append(['销售收到现金', 20])
        income = book.create_sheet('利润表')
        income.append(['项目名称', '本期金额'])
        income.append(['营业收入', 20])
        book.save(path)
        detected, skipped = discover_sheets([path])
        self.assertEqual([spec.sheet for spec in detected], ['利润表'])
        self.assertEqual(skipped, [])
        with patch('ledger_viewer.ui.messagebox.showinfo') as info:
            dialog = BatchImportDialog(self.app, [path])
            self.assertEqual([item[1] for item in dialog.items], ['利润表'])
            self.assertEqual(dialog.skipped, [])
            dialog.destroy()
            only = self.base / '现金流量表.csv'
            only.write_text('项目,本期金额\n现金流入,100\n', encoding='utf-8')
            dialog = BatchImportDialog(self.app, [only])
            self.assertFalse(dialog.winfo_exists())
            info.assert_not_called()
