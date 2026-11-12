import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from ledger_viewer.agent_import import create_book, inspect_file, load_plan, main, handoff_text, SKILL_PATH
from ledger_viewer.importer import file_hash
from ledger_viewer.store import LedgerStore


class AgentImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "单列金额.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "序时账"
        sheet.append(["日期", "凭证号数", "科目编码", "科目名称", "金额", "借贷方向"])
        sheet.append(["2026-09-01", "记1", "1002", "银行存款", 100.25, "借"])
        sheet.append([None, None, "6001", "主营业务收入", 100.25, "贷"])
        book.save(self.source)
        book.close()
        self.plan = {"version": 1, "sheets": [{"path": str(self.source), "sheet": "序时账",
                    "sha256": file_hash(self.source), "kind": "entry", "header_row": 1,
                    "mapping": {"date": 1, "voucher_no": 2, "account_code": 3, "account_name": 4, "amount": 5, "direction": 6}}]}
        self.plan_path = self.base / "方案.json"
        self.output = self.base / "账套.sqlite"
        self.save_plan()

    def tearDown(self):
        self.temp.cleanup()

    def save_plan(self):
        self.plan_path.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def test_single_amount_roundtrip_preserves_evidence_and_voucher(self):
        digest = file_hash(self.source)
        result = create_book(self.plan_path, self.output)
        self.assertEqual(result["sheets"][0]["imported_rows"], 2)
        self.assertEqual(file_hash(self.source), digest)
        store = LedgerStore(self.output)
        try:
            entries = store.db.execute("SELECT * FROM entries ORDER BY row_no").fetchall()
            self.assertEqual([(row["debit_cents"], row["credit_cents"]) for row in entries], [(10025, 0), (0, 10025)])
            self.assertEqual([row["row_no"] for row in entries], [2, 3])
            self.assertEqual([row["voucher_no"] for row in entries], ["记1", "记1"])
            self.assertEqual(store.sources()[0]["sha256"], digest)
            audit = json.loads(store.db.execute("SELECT value FROM ledger_meta WHERE key='agent_import_v1'").fetchone()[0])
            self.assertEqual(audit["plan"], self.plan)
            self.assertIn("100.25", entries[0]["raw_json"])
        finally:
            store.close()

    def test_no_overwrite(self):
        self.output.write_bytes(b"original")
        with self.assertRaisesRegex(ValueError, "已存在"):
            create_book(self.plan_path, self.output)
        self.assertEqual(self.output.read_bytes(), b"original")

    def test_changed_source_rejected(self):
        self.plan["sheets"][0]["sha256"] = "0" * 64
        self.save_plan()
        with self.assertRaisesRegex(ValueError, "指纹"):
            create_book(self.plan_path, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_direction_blocks_creation(self):
        self.plan["sheets"][0]["mapping"]["direction"] = 4
        self.plan["sheets"][0]["mapping"].pop("account_name")
        self.save_plan()
        with self.assertRaisesRegex(ValueError, "关键解析"):
            create_book(self.plan_path, self.output, accept_warnings=True)
        self.assertFalse(self.output.exists())

    def test_failure_on_later_sheet_leaves_no_book(self):
        other = self.base / "second.csv"
        other.write_text("日期,凭证号,科目编码,科目名称,金额,借贷方向\n2026-09-01,记2,1002,银行存款,1,借\n", encoding="utf-8")
        self.plan["sheets"].append(dict(self.plan["sheets"][0], path=str(other), sheet="CSV", sha256=file_hash(other)))
        self.save_plan()
        original = LedgerStore.import_sheet
        def fail_second(store, spec, **kwargs):
            if spec.path.resolve() == other.resolve():
                raise ValueError("模拟磁盘写入失败")
            return original(store, spec, **kwargs)
        with patch.object(LedgerStore, "import_sheet", fail_second), self.assertRaisesRegex(ValueError, "模拟"):
            create_book(self.plan_path, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(".dudledger-import-*")))

    def test_plan_rejects_invalid_column_numbers_and_unknown_fields(self):
        for value in (0, -1, True, 1.5, "1", 100):
            with self.subTest(value=value):
                self.plan["sheets"][0]["mapping"]["date"] = value
                self.save_plan()
                with self.assertRaises(ValueError):
                    load_plan(self.plan_path)
        self.plan["sheets"][0]["mapping"]["date"] = 1
        self.plan["sheets"][0]["mapping"]["made_up"] = 7
        self.save_plan()
        with self.assertRaisesRegex(ValueError, "未知字段"):
            load_plan(self.plan_path)

    def test_exclusions_and_ranges_require_reasons(self):
        for addition in ({"excluded_rows": {"3": ""}}, {"data_end_row": 2}):
            with self.subTest(addition=addition):
                self.plan["sheets"][0].update(addition)
                self.save_plan()
                with self.assertRaises(ValueError):
                    load_plan(self.plan_path)
                for key in addition:
                    del self.plan["sheets"][0][key]

    def test_duplicate_source_and_sheet_rejected(self):
        self.plan["sheets"].append(self.plan["sheets"][0].copy())
        self.save_plan()
        with self.assertRaisesRegex(ValueError, "重复导入"):
            create_book(self.plan_path, self.output)

    def test_inspect_range_and_cli_validate_are_read_only(self):
        result = inspect_file(self.source, "序时账", 2, 1)
        self.assertEqual(result["previews"][0]["rows"][0]["row"], 2)
        self.assertEqual(len(result["previews"][0]["rows"]), 1)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(["validate", str(self.plan_path)]), 0)
        self.assertEqual(json.loads(stream.getvalue())["reports"][0]["stats"]["imported_rows"], 2)
        self.assertFalse(self.output.exists())

    def test_handoff_uses_portable_skill_and_exact_selected_paths(self):
        content = handoff_text([(self.source, "序时账")], self.base)
        self.assertTrue(SKILL_PATH.is_file())
        self.assertIn(str(SKILL_PATH.resolve()), content)
        self.assertIn(str(self.source.resolve()), content)
        self.assertIn("序时账", content)

    def test_frozen_handoff_calls_application_instead_of_python(self):
        with patch('sys.frozen', True, create=True), patch('sys.executable', '/Applications/DudLedger.app/Contents/MacOS/DudLedger'):
            content = handoff_text([(self.source, '序时账')], self.base)
        context = json.loads(content.split('本机任务参数：\n', 1)[1])
        self.assertEqual(context['runtime'], 'portable')
        self.assertEqual(context['command_prefix'], ['/Applications/DudLedger.app/Contents/MacOS/DudLedger', '--agent-import'])
        self.assertNotIn('python', context)
        self.assertIn('不是 Python 解释器', content)

    def test_source_handoff_preserves_module_entry(self):
        with patch('sys.frozen', False, create=True):
            content = handoff_text([(self.source, '序时账')], self.base)
        context = json.loads(content.split('本机任务参数：\n', 1)[1])
        self.assertEqual(context['runtime'], 'source')
        self.assertEqual(context['command_prefix'][1:], ['-m', 'ledger_viewer.agent_import'])
        self.assertEqual(context['python'], context['command_prefix'][0])

    def test_warnings_require_acknowledgement_and_remain_in_audit(self):
        self.plan["sheets"][0]["mapping"].pop("voucher_no")
        self.save_plan()
        # The importer allows a missing voucher column, so use a concrete warning.
        from ledger_viewer.precheck import precheck, PrecheckIssue, SEVERITY_WARNING
        _, specs = load_plan(self.plan_path)
        report = precheck(specs[0])
        report.issues.append(PrecheckIssue(SEVERITY_WARNING, "样例待核事项"))
        with patch("ledger_viewer.agent_import.precheck", return_value=report):
            with self.assertRaisesRegex(ValueError, "仍有提醒"):
                create_book(self.plan_path, self.output)
            create_book(self.plan_path, self.output, accept_warnings=True)
        store = LedgerStore(self.output)
        try:
            self.assertIn("样例待核事项", store.db.execute("SELECT issues_json FROM import_prechecks").fetchone()[0])
        finally:
            store.close()
