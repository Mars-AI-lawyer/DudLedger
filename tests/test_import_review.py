"""Shared review correctness, privacy and file-change regression cases."""

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from openpyxl import Workbook

from ledger_viewer.background import Cancelled
from ledger_viewer.import_review import review_sheet, suggest_columns
from ledger_viewer.importer import ImportSpec
from ledger_viewer.jev_mapping import MappingSuggestion


class ImportReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'confidential.csv'
        self.path.write_text('公司内部标题\n科目编码,科目名称,借方金额,贷方金额\n1002,秘密客户,10,0\n')
        self.good = MappingSuggestion('entry', 2, {'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}, {}, (), 'fake')
        self.suggest = Mock(return_value=self.good)

    def review(self, **kwargs):
        return review_sheet(self.path, 'CSV', Event(), lambda _: None, suggest=self.suggest, api_key='fake', **kwargs)

    def test_first_rows_reach_model_without_file_metadata_and_no_book_is_created(self):
        review = self.review()
        self.assertTrue(review.ready)
        self.assertEqual(review.report.stats.imported_rows, 1)
        self.assertEqual(list(self.path.parent.glob('*.sqlite')), [])
        payload = self.suggest.call_args.args[0]
        self.assertEqual(len(payload['rows']), 3)
        self.assertEqual(payload['rows'][1], {'row': 2, 'cells': ['科目编码', '科目名称', '借方金额', '贷方金额']})
        self.assertIn('秘密客户', str(payload))
        self.assertNotIn(str(self.path), str(payload))

    def test_merged_balance_header_is_preserved_through_review(self):
        self.path = self.path.with_suffix('.xlsx')
        book = Workbook()
        sheet = book.active
        sheet.append(['科目编码', '科目名称', '本期发生额', None, '期末余额', None])
        sheet.append([None, None, '借方', '贷方', '借方', '贷方'])
        for region in ('A1:A2', 'B1:B2', 'C1:D1', 'E1:F1'):
            sheet.merge_cells(region)
        sheet.append(['1002', '银行存款', 10, 0, 10, 0])
        book.save(self.path)
        self.suggest.return_value = MappingSuggestion('balance', 2, {'account_code': 0, 'account_name': 1,
            'period_debit': 2, 'period_credit': 3, 'closing_debit': 4, 'closing_credit': 5}, {}, (), 'fake')
        review = review_sheet(self.path, sheet.title, Event(), lambda _: None, suggest=self.suggest, api_key='fake')
        self.assertTrue(review.ready)
        self.assertEqual(review.headers[2:], ['本期发生额借方', '本期发生额贷方', '期末余额借方', '期末余额贷方'])
        self.assertEqual(len(self.suggest.call_args.args[0]['rows']), 3)
        self.assertEqual(len(self.suggest.call_args.args[0]['merged_ranges']), 4)

    def test_explicit_header_works_identically_for_both_ui_entrypoints(self):
        self.path.write_text('X自定科目,X借入额,X贷出额\n银行存款,10,0\n')
        suggestion = MappingSuggestion('entry', 1, {'account_name': 0, 'debit': 1, 'credit': 2}, {}, (), 'fake')
        self.suggest.return_value = suggestion
        result = suggest_columns(self.path, 'CSV', self.suggest, 'fake')
        self.assertEqual(result, suggestion)
        result = suggest_columns(self.path, 'CSV', self.suggest, 'fake', header_row=1, kind='entry')
        self.assertEqual(result, suggestion)
        review = self.review(header_row=1, kind='entry')
        self.assertTrue(review.ready)

    def test_local_mode_does_not_call_model(self):
        spec = ImportSpec(self.path, 'CSV', 2, 'entry', self.good.mapping)
        review = self.review(manual_spec=spec)
        self.assertTrue(review.ready)
        self.suggest.assert_not_called()

    def test_out_of_range_mapping_is_rejected_before_precheck(self):
        self.suggest.return_value = MappingSuggestion('entry', 2, {'account_code': 0, 'debit': 90}, {}, (), 'fake')
        with patch('ledger_viewer.import_review.prepare_sheets') as prepare:
            with self.assertRaisesRegex(ValueError, '超出工作表范围'):
                self.review()
        prepare.assert_not_called()

    def test_file_changed_during_model_request_invalidates_result(self):
        def mutate(preview, key):
            self.path.write_text(self.path.read_text() + '1003,秘密客户,20,0\n')
            return self.good
        self.suggest.side_effect = mutate
        with self.assertRaisesRegex(ValueError, '原文件发生变化'):
            self.review()

    def test_file_changed_during_local_precheck_invalidates_result(self):
        from ledger_viewer.import_work import prepare_sheets
        def mutate(*args):
            outcome = prepare_sheets(*args)
            self.path.write_text(self.path.read_text() + '1003,客户,20,0\n')
            return outcome
        with patch('ledger_viewer.import_review.prepare_sheets', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, '原文件发生变化'):
                self.review()

    def test_cancelled_review_does_not_call_model(self):
        stop = Event()
        stop.set()
        with self.assertRaises(Cancelled):
            review_sheet(self.path, 'CSV', stop, lambda _: None, suggest=self.suggest, api_key='fake')
        self.suggest.assert_not_called()

    def test_empty_data_or_structural_change_blocks_review(self):
        self.path.write_text('标题\n科目编码,科目名称,借方金额,贷方金额\n')
        self.assertFalse(self.review().ready)
        self.path.write_text('标题\n科目编码,科目名称,借方金额,贷方金额\n1002,客户,10,0\n'
                             '科目名称,借方金额,贷方金额\n客户,20,0\n')
        review = self.review()
        self.assertFalse(review.ready)
        self.assertIn('疑点', review.problem)

    def test_three_and_four_level_headers_with_vertical_and_horizontal_merges(self):
        for depth in (3, 4):
            with self.subTest(depth=depth):
                self.path = self.path.with_suffix('.xlsx')
                book = Workbook()
                sheet = book.active
                sheet.append(['科目编码', '科目名称', '本期', None, '期末', None])
                for level in range(2, depth):
                    sheet.append([None, None, '发生额', None, '余额', None])
                sheet.append([None, None, '借方', '贷方', '借方', '贷方'])
                sheet.merge_cells(start_row=1, start_column=1, end_row=depth, end_column=1)
                sheet.merge_cells(start_row=1, start_column=2, end_row=depth, end_column=2)
                for level in range(1, depth):
                    for start in (3, 5):
                        sheet.merge_cells(start_row=level, start_column=start, end_row=level, end_column=start + 1)
                sheet.append(['1002', '银行存款', 10, 0, 10, 0])
                book.save(self.path)
                self.suggest.return_value = MappingSuggestion('balance', depth, {'account_code': 0, 'account_name': 1,
                    'period_debit': 2, 'period_credit': 3, 'closing_debit': 4, 'closing_credit': 5}, {}, (), 'fake')
                review = review_sheet(self.path, sheet.title, Event(), lambda _: None, suggest=self.suggest, api_key='fake')
                self.assertTrue(review.ready, review.problem)
                self.assertEqual(review.report.stats.imported_rows, 1)
                self.assertEqual(review.spec.header_row, depth)
                self.assertEqual(review.headers[2:], ['本期发生额借方', '本期发生额贷方', '期末余额借方', '期末余额贷方'])
                preview = self.suggest.call_args.args[0]
                self.assertEqual(len(preview['rows']), depth + 1)
                self.assertIn({'start_row': 1, 'end_row': depth, 'start_col': 1, 'end_col': 1}, preview['merged_ranges'])

    def test_header_cannot_end_in_middle_of_a_vertical_merge(self):
        self.path = self.path.with_suffix('.xlsx')
        book = Workbook()
        sheet = book.active
        sheet.append(['科目编码', '科目名称', '借方金额', '贷方金额'])
        sheet.merge_cells('A1:A3')
        sheet.append([None, None, None, None])
        sheet.append([None, None, None, None])
        sheet.append(['1002', '银行存款', 10, 0])
        book.save(self.path)
        self.suggest.return_value = MappingSuggestion('entry', 1, self.good.mapping, {}, (), 'fake')
        with self.assertRaisesRegex(ValueError, '合并区域中间'):
            review_sheet(self.path, sheet.title, Event(), lambda _: None, suggest=self.suggest, api_key='fake')

    def test_default_cloud_preview_never_sends_row_twenty_one(self):
        self.path.write_text('科目编码,科目名称,借方金额,贷方金额\n' +
                             '1002,允许的样本,10,0\n' * 19 + '1002,第二十一行不能发送,20,0\n')
        self.suggest.return_value = MappingSuggestion('entry', 1, self.good.mapping, {}, (), 'fake')
        review = self.review()
        self.assertTrue(review.ready)
        self.assertEqual(review.report.stats.imported_rows, 20)
        preview = self.suggest.call_args.args[0]
        self.assertEqual(len(preview['rows']), 20)
        self.assertNotIn('第二十一行不能发送', str(preview))

    def test_manually_specified_header_after_row_twenty_is_a_single_header_payload(self):
        self.path.write_text('标题\n' * 24 + '科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n')
        self.suggest.return_value = MappingSuggestion('entry', 25, self.good.mapping, {}, (), 'fake')
        review = self.review(header_row=25, kind='entry')
        self.assertTrue(review.ready)
        preview = self.suggest.call_args.args[0]
        self.assertEqual(preview['mode'], 'manual_header')
        self.assertEqual(len(preview['rows']), 1)
        self.assertEqual(preview['rows'][0]['row'], 25)
