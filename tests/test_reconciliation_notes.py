import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.checks import run_checks
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.reconciliation import empty_plan, save_plan
from ledger_viewer.store import LedgerStore
from ledger_viewer.voucher_notes import export_marks_md, list_marks, remove_mark, save_mark, save_mark_by_key


class ReconciliationNotesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / "case.sqlite")
        self.entry_file = self.base / "entries.csv"
        self.entry_file.write_text(
            "日期,会计年度,期间,凭证字,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
            "2025-01-03,2025,1,记,1,1001,库存现金,销售收款,500,0\n"
            "2025-01-03,2025,1,记,1,1002,银行存款,销售收款,1000,0\n"
            "2025-01-03,2025,1,记,1,6001,营业收入,销售收款,0,1500\n",
            encoding="utf-8",
        )
        self.balance_file = self.base / "balance.csv"
        self.balance_file.write_text(
            "科目编码,科目名称,期初借方,期初贷方,本期借方,本期贷方,期末借方,期末贷方\n"
            "1001,库存现金,0,0,500,0,500,0\n"
            "1002,银行存款,0,0,1000,0,1000,0\n"
            "6001,营业收入,0,0,0,1500,0,1500\n",
            encoding="utf-8",
        )
        self.bs_file = self.base / "bs.csv"
        self.bs_file.write_text("项目,期末余额\n货币资金,1500\n", encoding="utf-8")
        self.is_file = self.base / "is.csv"
        self.is_file.write_text("项目,本期金额\n营业收入,1400\n", encoding="utf-8")
        self.entry_id = self._import(self.entry_file, "entry")
        self.balance_id = self._import(self.balance_file, "balance")
        self.bs_id = self._import(self.bs_file, "balance_sheet")
        self.is_id = self._import(self.is_file, "income_statement")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _import(self, path, kind, replace=False):
        header, _, mapping = guess_header(path, "CSV", kind)
        self.store.import_sheet(ImportSpec(path, "CSV", header, kind, mapping), replace=replace)
        return next(row["id"] for row in self.store.sources() if row["original_path"] == str(path.resolve()) and row["kind"] == kind)

    def _plan(self):
        plan = empty_plan()
        plan.update(year="2025", period="1", entry_source_ids=[self.entry_id], balance_source_id=self.balance_id)
        plan["balance_sheet"] = {"source_id": self.bs_id, "amount_column": 1}
        plan["income_statement"] = {"source_id": self.is_id, "amount_column": 1}
        plan["mappings"] = [
            {"kind": "balance_sheet", "source_id": self.bs_id, "row_no": 2, "side": "left", "accounts": ["C:1001", "C:1002"], "direction": "debit"},
            {"kind": "income_statement", "source_id": self.is_id, "row_no": 2, "side": "left", "accounts": ["C:6001"], "direction": "credit"},
        ]
        save_plan(self.store, plan)
        return plan

    def test_confirmed_scope_compares_balance_and_both_statements(self):
        self._plan()
        findings = run_checks(self.store)
        differences = [item for item in findings if item.level == "差异"]
        self.assertEqual({item.category for item in differences}, {"利润表与余额表", "利润表与明细"})
        self.assertTrue(all("100.00" in item.detail and "is.csv" in item.detail for item in differences))
        self.assertFalse(any(item.category == "资产负债表与余额表" for item in differences))
        self.assertEqual(len(self.store.entries()), 3)
        self.assertEqual(len(self.store.voucher_lines(self.store.entries()[0]["id"])), 3)

    def test_explicit_entry_sources_prevent_cross_file_double_count(self):
        self._plan()
        duplicate = self.base / "other_entries.csv"
        duplicate.write_text("副本\n" + self.entry_file.read_text(encoding="utf-8"), encoding="utf-8")
        other_id = self._import(duplicate, "entry")
        scoped = [f for f in run_checks(self.store) if f.category == "余额表与明细" and f.level == "差异"]
        self.assertEqual(scoped, [])
        plan = self._plan()
        plan["entry_source_ids"] = [self.entry_id, other_id]
        save_plan(self.store, plan)
        self.assertTrue([f for f in run_checks(self.store) if f.category == "余额表与明细" and f.level == "差异"])

    def test_blank_statement_amount_is_pending_not_zero(self):
        self._plan()
        self.store.db.execute("UPDATE report_rows SET left_amount_1_cents=NULL WHERE source_id=? AND row_no=2", (self.bs_id,))
        findings = run_checks(self.store)
        self.assertTrue(any(f.category == "报表项目映射" and f.level == "待核" and "空白" in f.detail for f in findings))
        self.assertFalse(any(f.category == "资产负债表与余额表" and f.level == "差异" for f in findings))

    def test_marks_reexport_changes_and_reimport_review(self):
        entry = self.store.entries()[0]
        key = save_mark(self.store, entry["id"], "核实销售合同\n核实银行流水")
        self.assertEqual(list_marks(self.store)[0]["status"], "正常")
        target = self.base / "关注.md"
        self.assertEqual(export_marks_md(self.store, target), (1, 0))
        first = target.read_text(encoding="utf-8")
        self.assertIn("### 1001 库存现金", first)
        self.assertIn("### 1002 银行存款", first)
        self.assertEqual(first.count('<a id="voucher-1"></a>'), 1)
        self.assertIn("核实销售合同", first)
        self.assertIn("凭证日期：2025-01-03", first)
        save_mark_by_key(self.store, key, "已收到银行流水")
        export_marks_md(self.store, target)
        self.assertIn("已收到银行流水", target.read_text(encoding="utf-8"))
        self.assertNotIn("核实销售合同", target.read_text(encoding="utf-8"))
        self.entry_file.write_text(self.entry_file.read_text(encoding="utf-8").replace("销售收款,500,0", "销售收款,600,0"), encoding="utf-8")
        self.entry_id = self._import(self.entry_file, "entry", replace=True)
        self.assertEqual(list_marks(self.store)[0]["status"], "内容变化")
        self.assertEqual(export_marks_md(self.store, target), (0, 1))
        self.assertIn("暂未导出，请在软件中复核", target.read_text(encoding="utf-8"))
        save_mark_by_key(self.store, key, "已复核变更")
        self.assertEqual(export_marks_md(self.store, target), (1, 0))
        remove_mark(self.store, key)
        self.assertEqual(list_marks(self.store), [])

    def test_number_change_or_export_failure_keeps_old_record(self):
        key = save_mark(self.store, self.store.entries()[0]["id"], "原注释")
        target = self.base / "关注.md"
        target.write_text("旧内容", encoding="utf-8")
        with patch("ledger_viewer.voucher_notes.os.replace", side_effect=OSError("模拟写入失败")):
            with self.assertRaises(OSError):
                export_marks_md(self.store, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "旧内容")
        self.assertFalse(list(self.base.glob(".关注.md.*.tmp")))
        self.entry_file.write_text(self.entry_file.read_text(encoding="utf-8").replace(",记,1,", ",记,2,"), encoding="utf-8")
        self._import(self.entry_file, "entry", replace=True)
        self.assertEqual(list_marks(self.store)[0]["status"], "未匹配")
        self.assertEqual(export_marks_md(self.store, target), (0, 1))
        self.assertEqual(self.store.db.execute("SELECT note FROM voucher_marks WHERE voucher_key=?", (key,)).fetchone()[0], "原注释")

    def test_missing_voucher_number_marks_only_the_selected_row(self):
        path = self.base / "no_number.csv"
        path.write_text("日期,科目编码,科目名称,借方金额,贷方金额\n2025-01-05,1001,现金,20,0\n2025-01-05,6001,收入,0,20\n", encoding="utf-8")
        self._import(path, "entry")
        selected = next(row for row in self.store.entries() if row["filename"] == path.name)
        save_mark(self.store, selected["id"], "只关注此行")
        mark = list_marks(self.store)[0]
        self.assertEqual(len(mark["lines"]), 1)
        target = self.base / "single.md"
        self.assertEqual(export_marks_md(self.store, target), (1, 0))
        self.assertIn("无凭证号", target.read_text(encoding="utf-8"))
        self.assertIn("已导入分录：1 行", target.read_text(encoding="utf-8"))

    def test_unrelated_reimport_and_other_source_name_do_not_change_mark(self):
        save_mark(self.store, self.store.entries()[0]["id"], "核对")
        self.balance_file.write_text(self.balance_file.read_text(encoding="utf-8").replace("银行存款", "银行账户"), encoding="utf-8")
        self._import(self.balance_file, "balance", replace=True)
        self.assertEqual(list_marks(self.store)[0]["status"], "正常")
        self.entry_file.write_text(self.entry_file.read_text(encoding="utf-8") + "2025-01-04,2025,1,记,2,1001,库存现金,其他,1,0\n", encoding="utf-8")
        self._import(self.entry_file, "entry", replace=True)
        self.assertEqual(list_marks(self.store)[0]["status"], "正常")
        target = self.base / "stable.md"
        export_marks_md(self.store, target)
        self.assertIn("1002 银行存款", target.read_text(encoding="utf-8"))
        self.assertNotIn("银行账户", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
