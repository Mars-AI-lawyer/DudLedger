"""导入可靠性改进的验证：覆盖《DudLedger Excel 导入可靠性改进计划》第五节重点场景。

样本基线为程序内构造的合成样本；真实企业失败文件由使用者提供后另行只读分析，
真实业务成功率不在本文件报告范围。
"""

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from ledger_viewer.importer import (
    ImportSpec,
    classify_rows,
    classify_sheet,
    discover_sheets,
    formula_cells,
    guess_header,
    normalized_rows,
    rows,
)
from ledger_viewer.jev_mapping import preview_sheet
from ledger_viewer.precheck import SEVERITY_CRITICAL, SEVERITY_WARNING, precheck
from ledger_viewer.store import LedgerStore


ENTRY_HEADER = ["日期", "凭证字", "凭证号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额"]


class PrecheckTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / "case.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def entry_spec(self, path: Path, sheet: str = "CSV", header: int = 1, mapping: dict | None = None) -> ImportSpec:
        if mapping is None:
            mapping = {"date": 0, "voucher_type": 1, "voucher_no": 2, "account_code": 3, "account_name": 4, "summary": 5, "debit": 6, "credit": 7}
        return ImportSpec(path, sheet, header, "entry", mapping)


class DeepAndWideHeaderTest(PrecheckTestBase):
    def test_header_beyond_row_twenty_is_found_and_imported(self):
        path = self.base / "deep.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "序时账"
        for index in range(28):
            sheet.append([f"导出说明第 {index + 1} 行"] if index == 0 else [None])
        sheet.append(ENTRY_HEADER)
        sheet.append(["2026-06-01", "记", 1, "1002", "银行存款", "出资", 100, 0])
        sheet.append(["2026-06-01", "记", 1, "4001", "实收资本", "出资", 0, 100])
        book.save(path)
        header, headers, mapping = guess_header(path, "序时账", "entry")
        self.assertEqual(header, 29)
        self.assertEqual(len(mapping), 8)
        result = precheck(self.entry_spec(path, "序时账", 29))
        self.assertEqual(result.stats.imported_rows, 2)
        self.assertFalse(result.pending_rows)
        self.assertEqual(self.store.import_sheet(self.entry_spec(path, "序时账", 29)), 2)

    def test_wide_table_amount_beyond_column_twenty(self):
        path = self.base / "wide.csv"
        columns = ["日期", "凭证号", "科目编码", "科目名称", "摘要"] + [f"备用信息{i}" for i in range(1, 20)] + ["借方金额", "贷方金额"]
        debit_index, credit_index = len(columns) - 2, len(columns) - 1
        path.write_text(",".join(columns) + "\n" + ",".join(["2026-06-01", "1", "1002", "银行存款", "收款"] + ["x"] * 19 + ["250.5", "0"]) + "\n", encoding="utf-8")
        header, headers, mapping = guess_header(path, "CSV", "entry")
        self.assertEqual(mapping["debit"], debit_index)
        self.assertEqual(mapping["credit"], credit_index)
        preview = preview_sheet(path, "CSV")
        self.assertEqual(len(preview["rows"][0]["cells"]), len(columns))
        spec = self.entry_spec(path, header=1, mapping=mapping)
        result = precheck(spec)
        self.assertEqual(result.stats.debit_cents, 25050)
        self.assertEqual(self.store.import_sheet(spec), 1)


class MergedHeaderTest(PrecheckTestBase):
    def test_real_merged_two_level_balance_header_separates_period_year_closing(self):
        path = self.base / "merged.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "科目余额表"
        sheet.append(["科目余额表"])
        sheet.append(["科目编码", "科目名称", "期初余额", None, "本期发生", None, "本年累计", None, "期末余额", None])
        sheet.append([None, None, "借方", "贷方", "借方", "贷方", "借方", "贷方", "借方", "贷方"])
        sheet.merge_cells("C2:D2")
        sheet.merge_cells("E2:F2")
        sheet.merge_cells("G2:H2")
        sheet.merge_cells("I2:J2")
        sheet.append(["1002", "银行存款", 100, None, 20, 5, 120, 25, 115, None])
        book.save(path)
        header, headers, mapping = guess_header(path, "科目余额表", "balance")
        self.assertEqual(header, 3)
        self.assertEqual(mapping["opening_debit"], 2)
        self.assertEqual(mapping["period_debit"], 4)
        self.assertEqual(mapping["year_debit"], 6)
        self.assertEqual(mapping["closing_credit"], 9)
        self.assertEqual(headers[9], "期末余额贷方")
        spec = ImportSpec(path, "科目余额表", header, "balance", mapping)
        self.assertEqual(self.store.import_sheet(spec), 1)
        row = self.store.summary()[0]
        self.assertEqual(row["opening_debit"], 10000)
        self.assertEqual(row["reported_debit"], 2000)
        self.assertEqual(row["year_credit"], 2500)
        self.assertEqual(row["closing_debit"], 11500)


class SectionTest(PrecheckTestBase):
    def test_identical_repeated_header_merges_sections(self):
        path = self.base / "repeat.csv"
        lines = [",".join(ENTRY_HEADER),
                 "2026-06-01,记,1,1002,银行存款,收款,100,0",
                 "2026-06-01,记,1,4001,实收资本,收款,0,100",
                 ",".join(ENTRY_HEADER),
                 "2026-06-02,记,2,1002,银行存款,付款,0,60",
                 "2026-06-02,记,2,6602,管理费用,付款,60,0"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertEqual(result.stats.imported_rows, 4)
        self.assertEqual(result.stats.excluded.get("表头"), 2)  # 首个表头 + 重复表头
        self.assertFalse(result.pending_rows)
        self.assertEqual(self.store.import_sheet(self.entry_spec(path)), 4)

    def test_structurally_different_header_blocks_below_and_exclusion_restores(self):
        path = self.base / "mixed.csv"
        alien = ["摘要", "日期", "凭证号", "科目名称", "科目编码", "贷方金额", "借方金额"]
        lines = [",".join(ENTRY_HEADER),
                 "2026-06-01,记,1,1002,银行存款,收款,100,0",
                 ",".join(alien),
                 "收款,2026-06-02,9,其他,9999,5,5"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertEqual(result.pending_rows, [3, 4])
        self.assertTrue(any("结构变化" in issue.reason or "疑似" in issue.reason for issue in result.issues if issue.severity == SEVERITY_CRITICAL))
        excluded_spec = self.entry_spec(path).with_extra_exclusions((3, 4))
        fixed = precheck(excluded_spec)
        self.assertEqual(fixed.stats.imported_rows, 1)
        self.assertEqual(fixed.stats.excluded.get("人工排除"), 2)
        self.assertEqual(self.store.import_sheet(excluded_spec), 1)


class FormulaAndConversionTest(PrecheckTestBase):
    def test_missing_formula_cache_is_critical_and_import_rolls_back(self):
        path = self.base / "formula.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "序时账"
        sheet.append(ENTRY_HEADER)
        sheet.append(["2026-06-01", "记", 1, "1002", "银行存款", "收款", "=C2+98", 0])
        book.save(path)  # openpyxl 只写公式不写计算缓存
        self.assertIn((2, 6), formula_cells(path, "序时账"))
        spec = self.entry_spec(path, "序时账")
        result = precheck(spec)
        self.assertTrue(result.pending_rows)
        self.assertTrue(any("公式结果缺失" in issue.reason for issue in result.issues))
        with self.assertRaises(ValueError):
            self.store.import_sheet(spec)
        self.assertEqual(self.store.sources(), [])

    def test_gbk_csv_with_semicolon_delimiter_imports(self):
        path = self.base / "gbk.csv"
        path.write_bytes("科目编码;科目名称;借方金额;贷方金额\r\n2241;其他应付款;0;80\r\n1002;银行存款;80;0\r\n".encode("gbk"))
        detected = list(rows(path, "CSV"))
        self.assertEqual(detected[1][1][0], "2241")
        spec = ImportSpec(path, "CSV", 1, "entry", {"account_code": 0, "account_name": 1, "debit": 2, "credit": 3})
        self.assertEqual(self.store.import_sheet(spec), 2)

    def test_period_conflict_with_date_is_pending(self):
        path = self.base / "conflict.csv"
        path.write_text("日期,会计年度,期间,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                        "2026-06-01,2026,7,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        spec = ImportSpec(path, "CSV", 1, "entry", {"date": 0, "fiscal_year": 1, "period": 2, "voucher_no": 3, "account_code": 4, "account_name": 5, "summary": 6, "debit": 7, "credit": 8})
        result = precheck(spec)
        self.assertEqual(result.pending_rows, [2])
        self.assertIn("不一致", result.issues[0].reason)
        with self.assertRaises(ValueError):
            self.store.import_sheet(spec)

    def test_unparseable_date_keeps_raw_text_with_warning(self):
        path = self.base / "date.csv"
        path.write_text("日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                        "6月1日,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        spec = ImportSpec(path, "CSV", 1, "entry", {"date": 0, "voucher_no": 1, "account_code": 2, "account_name": 3, "summary": 4, "debit": 5, "credit": 6})
        result = precheck(spec)
        self.assertFalse(result.pending_rows)
        self.assertTrue(any("日期无法识别" in issue.reason for issue in result.issues if issue.severity == SEVERITY_WARNING))
        self.store.import_sheet(spec)
        entry = self.store.entries()[0]
        self.assertEqual(entry["entry_date"], "6月1日")

    def test_amount_without_account_on_first_row_is_pending(self):
        path = self.base / "noaccount.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,,,银行存款,收款,100,0\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertEqual(result.pending_rows, [2])
        self.assertIn("没有科目", result.issues[0].reason)

    def test_negative_and_zero_and_blank_amounts(self):
        path = self.base / "amounts.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,冲正,-100,0\n"
                        "2026-06-01,记,1,6602,管理费用,补提,0,50\n"
                        "2026-06-01,记,1,9999,待处理,无金额行,,\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertEqual(result.stats.imported_rows, 2)
        self.assertEqual(result.stats.debit_cents, -10000)
        self.assertEqual(result.stats.credit_cents, 5000)
        self.assertEqual(result.stats.excluded.get("无金额行"), 1)
        self.assertEqual(self.store.import_sheet(self.entry_spec(path)), 2)
        entry = self.store.entries(sort_by="date")[0]
        self.assertEqual(entry["debit_cents"], -10000)

    def test_footer_rows_are_excluded(self):
        path = self.base / "footer.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                        "第 1 页，共 3 页,,,,,,\n"
                        "打印时间：2026-06-30,,,,,,\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertEqual(result.stats.excluded.get("页脚"), 2)
        self.assertEqual(result.stats.imported_rows, 1)


class TotalComparisonTest(PrecheckTestBase):
    def test_matching_subtotal_reports_consistency(self):
        path = self.base / "totals.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                        "2026-06-01,记,1,4001,实收资本,收款,0,100\n"
                        ",,,,本月合计,,100,100\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        self.assertTrue(any("与原表第 4 行合计一致" in issue.reason for issue in result.issues))
        self.assertFalse(any(issue.severity == SEVERITY_CRITICAL for issue in result.issues))

    def test_mismatched_subtotal_is_warning_not_blocker(self):
        path = self.base / "mismatch.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                        ",,,,本月合计,,300,0\n", encoding="utf-8")
        result = precheck(self.entry_spec(path))
        mismatch = [issue for issue in result.issues if "不一致" in issue.reason]
        self.assertTrue(mismatch)
        self.assertEqual(mismatch[0].severity, SEVERITY_WARNING)
        self.assertFalse(result.pending_rows)
        self.assertEqual(self.store.import_sheet(self.entry_spec(path)), 1)

    def test_unmapped_amount_column_is_flagged(self):
        path = self.base / "hidden.csv"
        header_cols = ["日期", "凭证号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额", "对方科目金额"]
        lines = [",".join(header_cols)]
        for number in range(1, 26):
            lines.append(f"2026-06-{number:02d},{number},100{number},科目{number},摘要,{number},0,{number * 2}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        spec = ImportSpec(path, "CSV", 1, "entry", {"date": 0, "voucher_no": 1, "account_code": 2, "account_name": 3, "summary": 4, "debit": 5, "credit": 6})
        result = precheck(spec)
        self.assertTrue(any("第 8 列" in issue.column and "未映射" in issue.reason for issue in result.issues))


class TraceabilityTest(PrecheckTestBase):
    def test_precheck_does_not_touch_ledger(self):
        path = self.base / "plain.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        precheck(self.entry_spec(path))
        self.assertEqual(self.store.sources(), [])
        self.assertEqual(self.store.entries(), [])

    def test_fingerprint_changes_when_file_edited(self):
        path = self.base / "changed.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        first = precheck(self.entry_spec(path))
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,101,0\n", encoding="utf-8")
        second = precheck(self.entry_spec(path))
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_import_records_precheck_and_template_for_traceability(self):
        path = self.base / "tpl.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        spec = self.entry_spec(path)
        self.store.import_sheet(spec)
        source = self.store.sources()[0]
        record = self.store.precheck_for_source(source["id"])
        self.assertIsNotNone(record)
        self.assertIn("成功", record["verdict"])
        self.assertEqual(json.loads(record["stats_json"])["imported_rows"], 1)
        templates = self.store.list_templates()
        self.assertEqual(len(templates), 1)
        self.assertEqual(json.loads(templates[0]["mapping_json"])["mapping"], spec.mapping)

    def test_template_applies_to_same_structure_with_new_filename(self):
        first = self.base / "一月.csv"
        first.write_text(",".join(ENTRY_HEADER) + "\n2026-01-05,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        self.store.import_sheet(self.entry_spec(first))
        second = self.base / "二月-另一个文件名.csv"
        second.write_text("某某公司账\n" + ",".join(ENTRY_HEADER) + "\n2026-02-05,记,1,1002,银行存款,收款,300,0\n", encoding="utf-8")
        spec = classify_sheet(second, "CSV", self.store.template_candidates())
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind, "entry")
        self.assertEqual(spec.header_row, 2)
        self.assertEqual(self.store.import_sheet(spec), 1)
        entry = self.store.entries(fiscal_year="2026", period="2")[0]
        self.assertEqual(entry["debit_cents"], 30000)

    def test_template_not_applied_when_structure_changes(self):
        first = self.base / "a.csv"
        first.write_text(",".join(ENTRY_HEADER) + "\n2026-01-05,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        self.store.import_sheet(self.entry_spec(first))
        changed = self.base / "b.csv"
        changed.write_text("日期,凭证号,科目名称,科目编码,摘要,借方金额,贷方金额\n2026-02-05,1,银行存款,1002,收款,100,0\n", encoding="utf-8")
        spec = classify_sheet(changed, "CSV", self.store.template_candidates())
        # 结构变化 → 不按模板套用；本地规则按列名重新识别（科目名称在第 3 列，而非模板里的第 5 列）
        self.assertIsNotNone(spec)
        self.assertEqual(spec.mapping["account_name"], 2)
        self.assertNotEqual(spec.mapping, self.entry_spec(changed).mapping)

    def test_legacy_imports_marked_as_no_precheck_report(self):
        path = self.base / "old.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        self.store.import_sheet(self.entry_spec(path))
        # 模拟旧版导入：删掉预检记录
        with self.store.db:
            self.store.db.execute("DELETE FROM import_prechecks")
        source = self.store.sources()[0]
        self.assertIsNone(self.store.precheck_for_source(source["id"]))

    def test_quality_report_contains_sources_and_verdicts(self):
        path = self.base / "r.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        self.store.import_sheet(self.entry_spec(path))
        with self.store.db:
            self.store.db.execute("DELETE FROM import_prechecks")
        report = self.base / "报告.md"
        count = self.store.export_import_report(report)
        self.assertEqual(count, 1)
        text = report.read_text(encoding="utf-8")
        self.assertIn("导入质量报告", text)
        self.assertIn("无历史预检报告", text)

    def test_excluded_rows_validation_and_range(self):
        path = self.base / "range.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n2026-06-02,记,2,1002,银行存款,收款,50,0\n", encoding="utf-8")
        spec = ImportSpec(path, "CSV", 1, "entry", self.entry_spec(path).mapping, data_end_row=2)
        result = precheck(spec)
        self.assertEqual(result.stats.imported_rows, 1)
        with self.assertRaises(ValueError):
            ImportSpec(path, "CSV", 1, "entry", self.entry_spec(path).mapping, excluded_rows=(1,)).validate()
        with self.assertRaises(ValueError):
            ImportSpec(path, "CSV", 1, "entry", self.entry_spec(path).mapping, data_start_row=1).validate()


class StrictnessTest(PrecheckTestBase):
    def test_normalized_rows_raises_on_pending_and_classify_explains_every_row(self):
        path = self.base / "mixed_quality.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                        "\n"
                        ",,,,本月合计,,100,0\n"
                        "2026-06-01,记,2,1002,银行存款,坏金额,错,0\n", encoding="utf-8")
        spec = self.entry_spec(path)
        dispositions = {(d.row_no, d.disposition) for d in classify_rows(spec)}
        self.assertIn((1, "skip_header"), dispositions)
        self.assertIn((2, "import"), dispositions)
        self.assertIn((3, "skip_blank"), dispositions)
        self.assertIn((4, "skip_subtotal"), dispositions)
        self.assertIn((5, "pending"), dispositions)
        with self.assertRaises(ValueError):
            list(normalized_rows(spec))


class DiscoveryTemplateTest(PrecheckTestBase):
    def test_discover_uses_templates_first(self):
        seed = self.base / "seed.csv"
        seed.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        self.store.import_sheet(self.entry_spec(seed))
        other = self.base / "其他名字.csv"
        other.write_text("表头前标题行\n" + ",".join(ENTRY_HEADER) + "\n2026-07-01,记,1,1002,银行存款,收款,10,0\n", encoding="utf-8")
        detected, skipped = discover_sheets([other], self.store.template_candidates())
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0].header_row, 2)
        self.assertEqual(skipped, [])


class PrecheckDialogFlowTest(PrecheckTestBase):
    def setUp(self):
        super().setUp()
        try:
            from ledger_viewer.ui import Application

            self.app = Application()
        except Exception as exc:  # 图形界面不可用时跳过
            self.skipTest(f"图形界面不可用：{exc}")
            return
        self.window = next(iter(self.app.windows))
        self.window._use_book(self.base / "ui.sqlite")

    def tearDown(self):
        try:
            self.window._close()
        finally:
            super().tearDown()

    def _bad_csv(self) -> Path:
        path = self.base / "待确认.csv"
        path.write_text(",".join(ENTRY_HEADER) + "\n"
                        "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                        "2026-06-01,记,2,6602,管理费用,坏金额,错,0\n", encoding="utf-8")
        return path

    def test_single_import_shows_precheck_dialog_exclusion_then_imports(self):
        from unittest.mock import patch

        from ledger_viewer.ui import ImportDialog, PrecheckDialog

        dialog = ImportDialog(self.window, self._bad_csv())
        self.app.update()
        with patch("ledger_viewer.ui.messagebox.showinfo"):
            dialog.import_now()
            pre = next(w for w in dialog.winfo_children() if isinstance(w, PrecheckDialog))
            self.assertEqual(pre.work[0][1].pending_rows, [3])
            # 选中待处理行并排除 → 重新预检 → 继续导入
            children = pre.view.get_children("sheet0")
            pre.view.selection_set(children[0])
            pre._exclude_selected()
            self.assertEqual(pre.work[0][1].stats.excluded.get("人工排除"), 1)
            self.assertFalse(pre.work[0][1].pending_rows)
            pre._proceed()
            self.app.update()
        self.assertEqual(len(self.window.store.entries()), 1)
        self.assertEqual(self.window.store.sources()[0]["row_count"], 1)

    def test_batch_import_pauses_blocked_sheet_and_imports_clean_one(self):
        from unittest.mock import patch

        from ledger_viewer.ui import BatchImportDialog, PrecheckDialog

        good = self.base / "正常.csv"
        good.write_text(",".join(ENTRY_HEADER) + "\n2026-06-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
        bad = self._bad_csv().rename(self.base / "问题.csv")
        dialog = BatchImportDialog(self.window, [good, bad])
        shown: list[str] = []

        def record_info(title, message, **_kwargs):
            shown.append(message)

        with patch("ledger_viewer.ui.messagebox.showinfo", side_effect=record_info):
            dialog.use_ai.set(False)
            dialog._import_selected()
            pre = next(w for w in self.window.winfo_children() if isinstance(w, PrecheckDialog))
            self.assertEqual(len(pre.work), 2)
            pre._proceed()
            self.app.update()
        self.assertEqual(len(self.window.store.sources()), 1)
        self.assertEqual(self.window.store.sources()[0]["filename"], "正常.csv")
        self.assertTrue(any("暂停" in message for message in shown))


if __name__ == "__main__":
    unittest.main()
