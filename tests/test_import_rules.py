"""导入识别规则的回归样例：真实账套中踩到的版式缺口逐个覆盖。"""
import tempfile
import unittest
from pathlib import Path

from ledger_viewer.importer import (
    classify_rows, classify_sheet, guess_mapping, period_text, sheet_names,
)


def write_csv(folder, name: str, text: str) -> Path:
    path = Path(folder) / name
    path.write_text(text, encoding="utf-8")
    return path


class PeriodTextTest(unittest.TestCase):
    def test_year_dot_month_columns_parse_to_month(self):
        self.assertEqual(period_text("2024.1"), "1")
        self.assertEqual(period_text("2024.10"), "10")
        self.assertEqual(period_text("2024.12"), "12")

    def test_existing_forms_and_rejects_unchanged(self):
        self.assertEqual(period_text("12"), "12")
        self.assertEqual(period_text("12.0"), "12")
        with self.assertRaises(ValueError):
            period_text("2024.13")
        with self.assertRaises(ValueError):
            period_text("2024年1月")


class FooterRowTest(unittest.TestCase):
    def test_signature_and_printer_rows_are_footer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_csv(Path(temp), "账.csv",
                             "日期,凭证号,科目编码,科目名称,借方金额,贷方金额\n"
                             "2026-09-01,记1,1002,银行存款,10,0\n"
                             "财务负责人：\n"
                             "打印人：廖丽霞\n")
            spec = classify_sheet(path, sheet_names(path)[0], None)
            self.assertIsNotNone(spec)
            decisions = list(classify_rows(spec))
            footer = [d for d in decisions if d.disposition == "skip_footer"]
            self.assertEqual({d.row_no for d in footer}, {3, 4})

    def test_report_signature_block_is_footer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_csv(Path(temp), "报表.csv",
                             "项目,期末余额,年初余额\n"
                             "货币资金,100,80\n"
                             "企业负责人：\n"
                             "打印人：廖丽霞\n")
            spec = classify_sheet(path, sheet_names(path)[0], None)
            self.assertIsNotNone(spec)
            footer = {d.row_no for d in classify_rows(spec) if d.disposition == "skip_footer"}
            self.assertEqual(footer, {3, 4})


class BalanceDoubleDirectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = write_csv(self.temp.name, "余额.csv",
                              "科目编码,科目名称,期初余额,期初余额方向,借方发生额,贷方发生额,期末余额,期末余额方向\n"
                              "1001,库存现金,100,借,50,20,130,借\n"
                              "1002,应收A,50,贷,20,70,0,平\n"
                              "1003,应收C,0,平,100,0,100,贷\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_combined_header_guesses_both_directions(self):
        spec = classify_sheet(self.path, sheet_names(self.path)[0], None)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind, "balance")
        self.assertEqual(spec.mapping.get("opening_direction"), 3)
        self.assertEqual(spec.mapping.get("closing_direction"), 7)
        self.assertEqual(spec.mapping.get("opening_balance"), 2)
        self.assertEqual(spec.mapping.get("closing_balance"), 6)

    def test_each_period_uses_its_own_direction_and_flat_rows_import(self):
        spec = classify_sheet(self.path, sheet_names(self.path)[0], None)
        rows = {d.row_no: d.data for d in classify_rows(spec) if d.disposition == "import"}
        self.assertEqual(rows[2]["opening_debit_cents"], 10000)
        self.assertEqual(rows[2]["closing_debit_cents"], 13000)
        self.assertEqual(rows[3]["opening_credit_cents"], 5000)
        self.assertEqual(rows[3]["closing_debit_cents"] + rows[3]["closing_credit_cents"], 0)
        self.assertEqual(rows[4]["closing_credit_cents"], 10000)
        self.assertEqual(rows[4]["opening_debit_cents"] + rows[4]["opening_credit_cents"], 0)

    def test_flat_direction_with_nonzero_balance_stays_unresolved(self):
        path = write_csv(self.temp.name, "异常.csv",
                         "科目编码,科目名称,期初余额,期初余额方向,借方发生额,贷方发生额,期末余额,期末余额方向\n"
                         "1005,应收D,0,平,0,0,100,平\n")
        spec = classify_sheet(path, sheet_names(path)[0], None)
        self.assertIsNotNone(spec)
        pending = [d for d in classify_rows(spec) if d.disposition == "pending"]
        self.assertTrue(any("余额方向无法识别" in d.reason for d in pending))


class ReportGuessTest(unittest.TestCase):
    def test_income_statement_with_index_and_amount_headers(self):
        headers = ["项目", "行次", "发生额", "本年累计", "上年同期"]
        self.assertEqual(guess_mapping(headers, "income_statement"),
                         {"left_item": 0, "left_amount_1": 2, "left_amount_2": 3, "left_amount_3": 4})

    def test_index_column_never_becomes_a_structural_item(self):
        headers = ["项目", "行次", "期末数", "年初数"]
        mapping = guess_mapping(headers, "balance_sheet")
        self.assertEqual(mapping.get("left_item"), 0)
        self.assertNotIn("right_item", mapping)
        self.assertEqual(mapping.get("left_amount_1"), 2)


class HangtongStyleHeaderTest(unittest.TestCase):
    def test_last_level_account_and_local_currency_aliases(self):
        headers = ["科目末级编码", "科目末级名称", "期初余额方向", "期初余额本币",
                   "借方本币", "贷方本币", "期末余额方向", "期末余额本币"]
        mapping = guess_mapping(headers, "balance")
        self.assertEqual(mapping, {"account_code": 0, "account_name": 1, "opening_direction": 2,
                                   "opening_balance": 3, "period_debit": 4, "period_credit": 5,
                                   "closing_direction": 6, "closing_balance": 7})


if __name__ == "__main__":
    unittest.main()
