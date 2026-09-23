import tempfile
import unittest
import sqlite3
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
        self.assertEqual(migrated.db.execute("SELECT value FROM ledger_meta WHERE key='format'").fetchone()[0], "legal-ledger-viewer-v2")
        migrated.close()

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


if __name__ == "__main__":
    unittest.main()
