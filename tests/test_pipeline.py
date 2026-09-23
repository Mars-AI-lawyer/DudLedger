import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from ledger_viewer.checks import run_checks
from ledger_viewer.importer import ImportSpec, guess_header, money
from ledger_viewer.store import LedgerStore


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.book = self.base / "source.xlsx"
        workbook = Workbook()
        entries = workbook.active
        entries.title = "序时账"
        entries.append(["企业明细导出"])
        entries.append(["日期", "凭证字号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额", "往来单位", "辅助核算"])
        entries.append(["2025-01-03", "记-1", "1002", "银行存款", "股东出资", 1000, 0, "张三", "股东张三"])
        entries.append(["2025-01-03", "记-1", "4001", "实收资本", "股东出资", 0, 1000, "张三", "股东张三"])
        entries.append(["2025-01-04", "记-2", "1002", "银行存款", "借款", 500, 0, "甲公司", "甲公司"])
        entries.append(["2025-01-04", "记-2", "2241", "其他应付款", "借款", 0, 500, "甲公司", "甲公司"])
        entries.append([None, None, None, None, "本月合计", 1500, 1500, None, None])
        balance = workbook.create_sheet("科目余额表")
        balance.append(["科目编码", "科目名称", "期初借方", "期初贷方", "本期借方", "本期贷方", "期末借方", "期末贷方"])
        balance.append(["1002", "银行存款", 0, 0, 1500, 0, 1500, 0])
        balance.append(["4001", "实收资本", 0, 0, 0, 1000, 0, 1000])
        balance.append(["2241", "其他应付款", 0, 0, 0, 500, 0, 500])
        workbook.save(self.book)
        self.store = LedgerStore(self.base / "case.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_import_drill_and_reconcile(self):
        header, _, mapping = guess_header(self.book, "序时账", "entry")
        self.assertEqual(header, 2)
        self.assertEqual(self.store.import_sheet(ImportSpec(self.book, "序时账", header, "entry", mapping)), 4)
        header, _, mapping = guess_header(self.book, "科目余额表", "balance")
        self.assertEqual(self.store.import_sheet(ImportSpec(self.book, "科目余额表", header, "balance", mapping)), 3)
        summary = {row["code"]: row for row in self.store.summary()}
        self.assertEqual(summary["1002"]["detail_debit"], 150000)
        self.assertEqual(summary["1002"]["closing_debit"], 150000)
        one_entry = self.store.entries(account="C:1002")[0]
        self.assertEqual(len(self.store.voucher_lines(one_entry["id"])), 2)
        self.assertFalse([item for item in run_checks(self.store) if item.level in ("差异", "待核")])
        self.assertEqual(self.store.auxiliary_totals("C:1002")[0]["n"], 1)
        with self.assertRaises(Exception):
            self.store.import_sheet(ImportSpec(self.book, "序时账", 2, "entry", guess_header(self.book, "序时账", "entry")[2]))

    def test_decimal_and_transaction_rollback(self):
        self.assertEqual(money("(1,234.56)"), -123456)
        malformed = self.base / "bad.csv"
        malformed.write_text("科目编码,科目名称,借方,贷方\n1002,银行,100,0\n4001,资本,0,错值\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_sheet(ImportSpec(malformed, "CSV", 1, "entry", {"account_code": 0, "account_name": 1, "debit": 2, "credit": 3}))
        self.assertEqual(len(self.store.sources()), 0)
        self.assertEqual(len(self.store.summary()), 0)

    def test_single_balance_with_direction(self):
        path = self.base / "direction.csv"
        path.write_text("科目编码,科目名称,期初余额,本期借方,本期贷方,期末余额,余额方向\n1002,银行存款,100,50,0,150,借\n4001,实收资本,100,0,50,150,贷\n", encoding="utf-8")
        header, _, mapping = guess_header(path, "CSV", "balance")
        self.assertEqual(self.store.import_sheet(ImportSpec(path, "CSV", header, "balance", mapping)), 2)
        rows = {row["code"]: row for row in self.store.summary()}
        self.assertEqual(rows["1002"]["closing_debit"], 15000)
        self.assertEqual(rows["4001"]["closing_credit"], 15000)
        self.assertFalse([f for f in run_checks(self.store) if f.category == "余额滚动"])

    def test_detects_balance_difference_without_changing_source(self):
        path = self.base / "difference.csv"
        path.write_text("科目编码,科目名称,期初借方,期初贷方,本期借方,本期贷方,期末借方,期末贷方\n1002,银行存款,0,0,100,0,99,0\n", encoding="utf-8")
        header, _, mapping = guess_header(path, "CSV", "balance")
        self.store.import_sheet(ImportSpec(path, "CSV", header, "balance", mapping))
        findings = run_checks(self.store)
        self.assertTrue(any(f.category == "余额滚动" and "-1.00" in f.detail for f in findings))
        self.assertEqual(self.store.summary()[0]["closing_debit"], 9900)


if __name__ == "__main__":
    unittest.main()
