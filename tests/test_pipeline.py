import tempfile
import unittest
import sqlite3
from pathlib import Path

from openpyxl import Workbook

from ledger_viewer.checks import run_checks
from ledger_viewer.importer import ImportSpec, guess_header, money
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import DEFAULT_COLUMNS, visible_columns


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

    def test_continuation_rows_and_repeated_voucher_number_by_period(self):
        path = self.base / "realistic.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(["日期", "会计年度", "期间", "凭证字", "凭证号", "摘要", "科目编码", "科目全名", "核算维度", "币别", "原币金额", "借方金额", "贷方金额", "制单"])
        sheet.append(["2026/6/1", 2026, 6, "记", 1, "收到款项", "1002.01", "银行存款", "甲公司", "人民币", 100, 100, None, "甲"])
        sheet.append([None, None, None, None, None, "收到款项", "2241.05", "其他应付款", "甲公司", "人民币", 100, None, 100, None])
        sheet.append(["2026/7/1", 2026, 7, "记", 1, "收到款项", "1002.01", "银行存款", "乙公司", "人民币", 200, 200, None, "乙"])
        sheet.append([None, None, None, None, None, "收到款项", "2241.05", "其他应付款", "乙公司", "人民币", 200, None, 200, None])
        workbook.save(path)
        header, _, mapping = guess_header(path, "Sheet1", "entry")
        self.assertEqual(len(mapping), 14)
        self.assertNotIn("counterparty", mapping)
        self.assertEqual(self.store.import_sheet(ImportSpec(path, "Sheet1", header, "entry", mapping)), 4)
        june = self.store.entries(account="C:1002.01")[0]
        july = self.store.entries(account="C:1002.01")[1]
        june_lines = self.store.voucher_lines(june["id"])
        self.assertEqual(len(june_lines), 2)
        self.assertEqual({line["period"] for line in june_lines}, {"6"})
        self.assertEqual({line["entry_date"] for line in june_lines}, {"2026-06-01"})
        self.assertEqual({line["currency"] for line in june_lines}, {"人民币"})
        self.assertEqual(len(self.store.voucher_lines(july["id"])), 2)
        self.assertEqual(len(self.store.entries(fiscal_year="2026", period="6")), 2)
        exported = self.base / "june.csv"
        self.assertEqual(self.store.export_entries(exported, fiscal_year="2026", period="6"), 2)
        self.assertIn("会计年度,期间", exported.read_text(encoding="utf-8-sig"))
        self.assertFalse([f for f in run_checks(self.store) if f.category == "凭证借贷"])
        self.assertEqual(self.store.import_sheet(ImportSpec(path, "Sheet1", header, "entry", mapping), replace=True), 4)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(len(self.store.entries()), 4)

    def test_failed_reimport_keeps_previous_records(self):
        header, _, mapping = guess_header(self.book, "序时账", "entry")
        original = ImportSpec(self.book, "序时账", header, "entry", mapping)
        self.store.import_sheet(original)
        wrong = dict(mapping)
        wrong["debit"] = wrong["summary"]
        del wrong["summary"]
        with self.assertRaises(ValueError):
            self.store.import_sheet(ImportSpec(self.book, "序时账", header, "entry", wrong), replace=True)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(len(self.store.entries()), 4)

    def test_existing_v1_book_adds_period_columns(self):
        path = self.base / "old.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE ledger_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO ledger_meta VALUES('format','legal-ledger-viewer-v1')")
        db.execute("""CREATE TABLE entries(id INTEGER PRIMARY KEY,source_id INTEGER,row_no INTEGER,entry_date TEXT,voucher_no TEXT,
                   voucher_type TEXT,account_key TEXT,summary TEXT,counterparty TEXT,auxiliary TEXT,debit_cents INTEGER,credit_cents INTEGER,raw_json TEXT)""")
        db.execute("INSERT INTO entries VALUES(1,1,2,'2026-06-01','1','记','C:1002','测试','','',100,0,'[]')")
        db.commit()
        db.close()
        migrated = LedgerStore(path)
        row = migrated.db.execute("SELECT fiscal_year,period FROM entries WHERE id=1").fetchone()
        self.assertEqual(tuple(row), ("2026", "6"))
        self.assertEqual(migrated.db.execute("SELECT value FROM ledger_meta WHERE key='format'").fetchone()[0], "legal-ledger-viewer-v3")
        self.assertTrue(migrated.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_rows'").fetchone())
        migrated.close()

    def test_existing_v2_book_gets_report_table(self):
        self.store.db.execute("UPDATE ledger_meta SET value='legal-ledger-viewer-v2' WHERE key='format'")
        self.store.db.execute("DROP TABLE report_rows")
        self.store.db.commit()
        self.store.close()
        self.store = LedgerStore(self.base / "case.sqlite")
        self.assertEqual(self.store.setting("voucher_open_mode", "replace"), "replace")
        self.assertTrue(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_rows'").fetchone())

    def test_balance_sheet_both_sides_preserves_title_blank_zero_and_source_rows(self):
        path = self.base / "statements.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "资产负债表"
        sheet.append(["资产负债表"])
        sheet.append(["项目", "期末余额", "上年年末余额", "项目", "期末余额", "上年年末余额"])
        sheet.append(["流动资产：", None, None, "流动负债：", None, None])
        sheet.append(["货币资金", 0, None, "短期借款", 50, 20])
        sheet.append(["资产合计", 0, 100, "负债合计", 50, 20])
        workbook.save(path)
        header, _, mapping = guess_header(path, "资产负债表", "balance_sheet")
        self.assertEqual(header, 2)
        self.assertEqual(len(mapping), 6)
        self.assertEqual(self.store.import_sheet(ImportSpec(path, "资产负债表", header, "balance_sheet", mapping)), 4)
        source = self.store.sources()[0]
        report = self.store.report_rows(source["id"])
        self.assertEqual([row["row_no"] for row in report], [1, 3, 4, 5])
        self.assertEqual(report[0]["left_item"], "资产负债表")
        self.assertEqual(report[1]["left_item"], "流动资产：")
        self.assertEqual(report[2]["left_amount_1_cents"], 0)
        self.assertIsNone(report[2]["left_amount_2_cents"])
        self.assertEqual(report[2]["right_amount_1_cents"], 5000)
        self.assertFalse(any("报表" in finding.category for finding in run_checks(self.store)))

    def test_vertical_balance_sheet_accepts_one_amount_and_keeps_subtotals(self):
        path = self.base / "vertical.csv"
        path.write_text("资产负债表\n项目,期末余额\n流动资产：,\n货币资金,0\n资产总计,100\n负债总计,—\n", encoding="utf-8")
        header, _, mapping = guess_header(path, "CSV", "balance_sheet")
        self.assertEqual(mapping, {"left_item": 0, "left_amount_1": 1})
        self.store.import_sheet(ImportSpec(path, "CSV", header, "balance_sheet", mapping))
        report = self.store.report_rows(self.store.sources()[0]["id"])
        self.assertEqual([row["left_item"] for row in report], ["资产负债表", "流动资产：", "货币资金", "资产总计", "负债总计"])
        self.assertIsNone(report[1]["left_amount_1_cents"])
        self.assertEqual(report[2]["left_amount_1_cents"], 0)
        self.assertIsNone(report[4]["left_amount_1_cents"])

    def test_income_statement_replacement_rolls_back_and_settings_are_isolated(self):
        path = self.base / "income.csv"
        path.write_text("利润表\n项目,本期金额,上期金额\n营业收入,100,\n营业成本,0,20\n营业利润,100,0\n", encoding="utf-8")
        header, _, mapping = guess_header(path, "CSV", "income_statement")
        self.assertEqual(header, 2)
        self.assertEqual(self.store.import_sheet(ImportSpec(path, "CSV", header, "income_statement", mapping)), 4)
        source = self.store.sources()[0]
        self.assertIsNone(self.store.report_rows(source["id"])[1]["left_amount_2_cents"])
        self.assertEqual(self.store.report_rows(source["id"])[2]["left_amount_1_cents"], 0)
        path.write_text("利润表\n项目,本期金额,上期金额\n营业收入,错误,50\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_sheet(ImportSpec(path, "CSV", header, "income_statement", mapping), replace=True)
        self.assertEqual(len(self.store.sources()), 1)
        self.assertEqual(len(self.store.report_rows(source["id"])), 4)
        other = LedgerStore(self.base / "other.sqlite")
        try:
            self.store.save_setting("voucher_open_mode", "multiple")
            self.store.save_voucher_widths({"summary": 340})
            self.assertEqual(other.setting("voucher_open_mode", "replace"), "replace")
            self.assertEqual(other.voucher_widths(), {})
            self.assertEqual(self.store.voucher_widths()["summary"], 340)
            self.store.reset_voucher_widths()
            self.assertEqual(self.store.voucher_widths(), {})
        finally:
            other.close()

    def test_multiple_balance_snapshots_are_not_added_together(self):
        header, _, mapping = guess_header(self.book, "科目余额表", "balance")
        self.store.import_sheet(ImportSpec(self.book, "科目余额表", header, "balance", mapping))
        first_id = self.store.sources()[0]["id"]
        later = self.base / "later.csv"
        later.write_text("科目编码,科目名称,期末借方,期末贷方\n1002,银行存款,2000,0\n", encoding="utf-8")
        header, _, mapping = guess_header(later, "CSV", "balance")
        self.store.import_sheet(ImportSpec(later, "CSV", header, "balance", mapping))
        self.assertEqual({row["code"]: row for row in self.store.summary()}["1002"]["closing_debit"], 200000)
        self.assertEqual({row["code"]: row for row in self.store.summary(balance_source_id=first_id)}["1002"]["closing_debit"], 150000)
        self.assertTrue(any(f.category == "余额表来源" for f in run_checks(self.store)))

    def test_display_columns_survive_reopening_without_changing_entries(self):
        header, _, mapping = guess_header(self.book, "序时账", "entry")
        self.store.import_sheet(ImportSpec(self.book, "序时账", header, "entry", mapping))
        before = len(self.store.entries())
        self.assertNotIn("aux", DEFAULT_COLUMNS["detail"])
        self.assertNotIn("currency", DEFAULT_COLUMNS["detail"])
        self.store.save_display_columns("detail", ["summary", "debit", "currency"])
        self.store.close()
        self.store = LedgerStore(self.base / "case.sqlite")
        self.assertEqual(self.store.display_columns("detail"), ["summary", "debit", "currency"])
        self.assertEqual(visible_columns("detail", self.store.display_columns("detail")), ["summary", "currency", "debit"])
        self.assertEqual(visible_columns("detail", ["obsolete"]), DEFAULT_COLUMNS["detail"])
        self.assertEqual(len(self.store.entries()), before)


if __name__ == "__main__":
    unittest.main()
