import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ledger_viewer.import_cascade import _excess_issues, suggest_cascade
from ledger_viewer.jev_mapping import MappingSuggestion, preview_sheet
from ledger_viewer.precheck import PrecheckIssue, SEVERITY_CRITICAL, SEVERITY_WARNING


class ImportCascadeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "账.csv"
        self.path.write_text("日期,凭证号,科目编码,科目名称,借方金额,贷方金额\n"
                             "2026-09-01,记1,1002,银行存款,10,0\n", encoding="utf-8")
        self.good = MappingSuggestion("entry", 1, {"date": 0, "voucher_no": 1, "account_code": 2,
                                      "account_name": 3, "debit": 4, "credit": 5}, {}, (), "test")
        self.keys = {"jev": "jev-secret", "deepseek": "deepseek-secret"}

    def tearDown(self):
        self.temp.cleanup()

    def run_cascade(self, first, second, **kwargs):
        return suggest_cascade(self.path, "CSV", preview_sheet(self.path, "CSV"),
                               self.keys, first, second, **kwargs)

    def test_jev_passes_without_deepseek_call(self):
        first, second = Mock(return_value=self.good), Mock()
        self.assertEqual(self.run_cascade(first, second), self.good)
        first.assert_called_once()
        second.assert_not_called()

    def test_invalid_mapping_escalates_once(self):
        first, second = Mock(return_value=MappingSuggestion("entry", 1, {}, {}, (), "test")), Mock(return_value=self.good)
        self.assertEqual(self.run_cascade(first, second), self.good)
        second.assert_called_once()
        self.assertNotIn(str(self.path), str(second.call_args))

    def test_model_warning_escalates(self):
        first = Mock(return_value=MappingSuggestion("entry", 1, self.good.mapping, {}, ("借贷方向不确定",), "test"))
        second = Mock(return_value=self.good)
        self.run_cascade(first, second)
        second.assert_called_once()

    def test_notice_alone_does_not_escalate(self):
        first = Mock(return_value=MappingSuggestion("entry", 1, self.good.mapping, {}, (), "test",
                                                    ("“voucher_type”与“voucher_no”选择了同一列",)))
        second = Mock()
        self.assertEqual(self.run_cascade(first, second), first.return_value)
        second.assert_not_called()

    def test_critical_matching_local_baseline_passes(self):
        """整表坏行本地规则同样命中：建议不劣于基线即通过，坏行由导入预检把关。"""
        with self.path.open("a", encoding="utf-8") as handle:
            for _ in range(25):
                handle.write("2026-09-02,记2,1002,银行存款,1,0\n")
            handle.write("2026-09-02,记2,1002,银行存款,金额有误,0\n")
        second = Mock()
        self.assertEqual(self.run_cascade(Mock(return_value=self.good), second), self.good)
        second.assert_not_called()

    def test_extra_unmapped_amount_column_escalates(self):
        wider = Path(self.temp.name) / "宽.csv"
        wider.write_text("日期,凭证号,科目编码,科目名称,借方金额,贷方金额,合计金额\n" +
                         "2026-09-01,记1,1002,银行存款,10,5,10\n" * 25, encoding="utf-8")
        preview = preview_sheet(wider, "CSV")
        sloppy = MappingSuggestion("entry", 1, {"date": 0, "voucher_no": 1, "account_code": 2,
                                                "account_name": 3, "debit": 4}, {}, (), "test")
        first, second = Mock(return_value=sloppy), Mock(return_value=self.good)
        result = suggest_cascade(wider, "CSV", preview, self.keys, first, second)
        self.assertEqual(result, self.good)
        second.assert_called_once()

    def test_local_unrecognized_table_needs_zero_critical(self):
        plain = Path(self.temp.name) / "流水.csv"
        plain.write_text("列1,列2,列3,列4,列5,列6,列7\n" +
                         "2026-09-01,记1,1002,银行存款,10,0,备注\n" * 3 +
                         "2026-09-02,记2,1002,银行存款,10,0,金额有误\n", encoding="utf-8")
        preview = preview_sheet(plain, "CSV")
        bad = MappingSuggestion("entry", 1, {"date": 0, "voucher_no": 1, "account_code": 2,
                                             "account_name": 3, "debit": 6, "credit": 5}, {}, (), "test")
        first, second = Mock(return_value=bad), Mock(return_value=self.good)
        result = suggest_cascade(plain, "CSV", preview, self.keys, first, second)
        self.assertEqual(result, self.good)
        second.assert_called_once()

    def test_invalid_suggestions_from_both_providers_fail(self):
        empty = MappingSuggestion("entry", 1, {}, {}, (), "test")
        with self.assertRaisesRegex(ValueError, "复制给我的 Agent"):
            self.run_cascade(Mock(return_value=empty), Mock(return_value=empty))

    def test_failure_redacts_both_keys(self):
        with self.assertRaises(ValueError) as error:
            self.run_cascade(Mock(side_effect=ValueError("jev-secret")), Mock(side_effect=ValueError("deepseek-secret")))
        self.assertNotIn("jev-secret", str(error.exception))
        self.assertNotIn("deepseek-secret", str(error.exception))

    def test_cancel_before_escalation_does_not_send_to_second_provider(self):
        cancelled = []
        def fail(*args):
            cancelled.append(True)
            raise ValueError("识别失败")
        second = Mock()
        with self.assertRaisesRegex(ValueError, "已取消"):
            self.run_cascade(fail, second, cancelled=lambda: bool(cancelled))
        second.assert_not_called()

    def test_missing_jev_key_uses_deepseek(self):
        self.keys.pop("jev")
        first, second = Mock(), Mock(return_value=self.good)
        self.run_cascade(first, second)
        first.assert_not_called()
        second.assert_called_once()

    def test_single_amount_direction_passes(self):
        self.path.write_text("日期,凭证号,科目编码,科目名称,金额,借贷方向\n"
                             "2026-09-01,记1,1002,银行存款,10,借\n", encoding="utf-8")
        suggestion = MappingSuggestion("entry", 1, {"date": 0, "voucher_no": 1, "account_code": 2,
                                        "account_name": 3, "amount": 4, "direction": 5}, {}, (), "test")
        second = Mock()
        self.assertEqual(self.run_cascade(Mock(return_value=suggestion), second), suggestion)
        second.assert_not_called()


class ExcessIssuesTest(unittest.TestCase):
    def test_same_category_same_severity_counts_against_baseline(self):
        baseline = [PrecheckIssue(SEVERITY_WARNING, "第 7 列「合计金额」未映射，但 25 个非空单元格大多像金额")]
        same = [PrecheckIssue(SEVERITY_WARNING, "第 7 列「合计金额」未映射，但 25 个非空单元格大多像金额")]
        self.assertEqual(_excess_issues(same, baseline), [])
        extra = baseline + [PrecheckIssue(SEVERITY_WARNING, "第 8 列「备注金额」未映射，但 9 个非空单元格大多像金额")]
        # 同一类别超出基线时整类计为超额，全部返回供升级原因展示
        self.assertEqual(len(_excess_issues(extra, baseline)), 2)

    def test_severity_escalation_and_new_categories_are_excess(self):
        baseline = [PrecheckIssue(SEVERITY_WARNING, "第 7 列「合计金额」未映射，但 25 个非空单元格大多像金额")]
        worse = [PrecheckIssue(SEVERITY_CRITICAL, "第 7 列「合计金额」未映射，但 25 个非空单元格大多像金额")]
        self.assertEqual(len(_excess_issues(worse, baseline)), 1)
        other = [PrecheckIssue(SEVERITY_WARNING, "会计期间无法识别：2024.1")]
        self.assertEqual(len(_excess_issues(baseline + other, baseline)), 1)

    def test_improved_severity_is_not_excess_even_with_more_rows(self):
        baseline = [PrecheckIssue(SEVERITY_CRITICAL, "金额无法识别：金额有误")]
        improved = [PrecheckIssue(SEVERITY_WARNING, "金额无法识别：金额有误"),
                    PrecheckIssue(SEVERITY_WARNING, "金额无法识别：金额有误")]
        self.assertEqual(_excess_issues(improved, baseline), [])


if __name__ == "__main__":
    unittest.main()
