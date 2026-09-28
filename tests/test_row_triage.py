"""疑点行自动排除的规则与 AI 分诊解析测试（不发网络请求）。"""

import json
import tempfile
import unittest
from pathlib import Path

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.precheck import precheck
from ledger_viewer.row_triage import pending_reasons, program_guard, resolve_pending, row_excerpts, triage_rows
from ledger_viewer.store import LedgerStore

ENTRY_HEADER = ["日期", "凭证字", "凭证号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额"]


class RowTriageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / "case.sqlite")
        self.path = self.base / "疑点.csv"
        self.path.write_text(",".join(ENTRY_HEADER) + "\n"
                             "2026-06-01,记,1,1002,银行存款,收款,100,0\n"
                             "2026-06-01,记,2,6602,管理费用,坏金额,错,0\n", encoding="utf-8")
        header, _, mapping = guess_header(self.path, "CSV", "entry")
        self.spec = ImportSpec(self.path, "CSV", header, "entry", mapping)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_program_guard_rules(self):
        self.assertTrue(program_guard({3: "金额无法识别"}, 100))
        # 结构变化（表内另一张表）不敢自动排除
        self.assertFalse(program_guard({3: "位于第 2 行疑似表头（结构变化）之下"}, 100))
        # 疑点行过多
        self.assertFalse(program_guard({row: "金额无法识别" for row in range(3, 25)}, 1000))
        # 疑点行占比：100 行可导入时阈值 100//20=5 行
        self.assertTrue(program_guard({row: "金额无法识别" for row in range(3, 5)}, 100))
        self.assertFalse(program_guard({row: "金额无法识别" for row in range(3, 13)}, 100))

    def test_resolve_pending_with_ai_labels(self):
        outcome = resolve_pending(self.spec, {3: "金额无法识别"}, 100, {3: ("junk", "表尾批注")})
        self.assertTrue(outcome.resolved)
        self.assertIn(3, outcome.excluded)
        self.assertIn("AI 判断为非账簿数据", outcome.excluded[3])
        self.assertTrue(outcome.ai_used)
        # AI 未覆盖的行保守按真实数据保留
        outcome = resolve_pending(self.spec, {3: "金额无法识别", 4: "结构变化"}, 100, {3: ("junk", "批注")})
        self.assertFalse(outcome.resolved)
        self.assertIn(4, outcome.held)

    def test_resolve_pending_program_fallback(self):
        outcome = resolve_pending(self.spec, {3: "金额无法识别"}, 100, None)
        self.assertTrue(outcome.resolved)
        self.assertFalse(outcome.ai_used)
        self.assertIn("程序规则自动排除", outcome.excluded[3])
        outcome = resolve_pending(self.spec, {3: "结构变化"}, 100, None)
        self.assertFalse(outcome.resolved)
        self.assertIn(3, outcome.held)

    def test_triage_rows_parses_and_validates_row_numbers(self):
        def chat(_key, payload):
            return '{"rows": [{"row": 3, "action": "junk", "reason": "表尾批注"}]}'

        labels = triage_rows(self.spec, {3: "金额无法识别"}, {3: "管理费用 | 坏金额 | 错"}, "key", chat=chat)
        self.assertEqual(labels, {3: ("junk", "表尾批注")})

        def stranger(key, payload):
            return '{"rows": [{"row": 99, "action": "junk", "reason": "无关行"}]}'

        with self.assertRaises(ValueError):
            triage_rows(self.spec, {3: "金额无法识别"}, {3: "..."}, "key", chat=stranger)

    def test_pending_reasons_and_excerpts_from_real_precheck(self):
        result = precheck(self.spec)
        reasons = pending_reasons(result)
        self.assertEqual(list(reasons), [3])
        self.assertIn("金额", reasons[3])
        excerpts = row_excerpts(self.spec, set(reasons))
        self.assertIn("管理费用", excerpts[3])

    def test_import_records_ai_exclusion_reason(self):
        outcome = resolve_pending(self.spec, pending_reasons(precheck(self.spec)), 1, {3: ("junk", "批注")})
        spec2 = self.spec.with_extra_exclusions(tuple(outcome.excluded))
        self.assertEqual(self.store.import_sheet(spec2, exclusion_reasons=outcome.excluded), 1)
        record = self.store.precheck_for_source(self.store.sources()[0]["id"])
        stats = json.loads(record["stats_json"])
        self.assertEqual(stats["excluded"].get("疑点排除"), 1)
        issues = json.loads(record["issues_json"])
        self.assertTrue(any("AI 判断为非账簿数据" in issue.get("reason", "") for issue in issues))


if __name__ == "__main__":
    unittest.main()
