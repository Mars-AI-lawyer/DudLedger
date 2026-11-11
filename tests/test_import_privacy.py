import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from ledger_viewer.import_preview import preview_for_ai, preview_headers
from ledger_viewer.ui import Application, BatchImportDialog, ImportDialog, ai_tools, suggest_for_import


class ImportPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "保密客户名称.csv"
        self.path.write_text("秘密公司\n日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                             "2026-01-01,秘密凭证,1002,银行存款,秘密交易摘要,1234567.89,0\n", encoding="utf-8")

    def test_authorized_first_rows_request_does_not_add_file_metadata(self):
        def fake_chat(key, payload):
            encoded = json.dumps(payload, ensure_ascii=False)
            for metadata in (self.path.name, str(self.path.parent)):
                self.assertNotIn(metadata, encoded)
            for sample in ("秘密交易摘要", "1234567.89", "2026-01-01"):
                self.assertIn(sample, encoded)
            return json.dumps({"kind": "entry", "header_row": 2,
                               "mapping": {"account_code": 3, "account_name": 4, "debit": 6, "credit": 7}})

        from ledger_viewer.deepseek_mapping import suggest_mapping
        def suggest(preview, key):
            self.assertEqual(len(preview["rows"]), 3)
            self.assertEqual(preview["sheet"], "工作表")
            return suggest_mapping(preview, key, chat=fake_chat)

        with patch("ledger_viewer.ui.deepseek_suggest_mapping", side_effect=suggest), \
             patch("ledger_viewer.jev_mapping.suggest_mapping") as jev:
            result = suggest_for_import(self.path, "CSV", None, "fake-key")
        self.assertEqual(result.header_row, 2)
        jev.assert_not_called()

    def test_merged_headers_are_one_logical_row(self):
        path = self.path.with_suffix(".xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "秘密企业余额"
        sheet.append(["科目编码", "科目名称", "本期发生额", None, "期末余额", None])
        sheet.append([None, None, "借方", "贷方", "借方", "贷方"])
        for area in ("A1:A2", "B1:B2", "C1:D1", "E1:F1"):
            sheet.merge_cells(area)
        sheet.append(["1002", "秘密户名", 1234567, 0, 1234567, 0])
        workbook.save(path)
        preview = preview_headers(path, sheet.title)
        self.assertEqual(preview, {"sheet": "工作表", "rows": [{"row": 2, "cells": [
            "科目编码", "科目名称", "本期发生额借方", "本期发生额贷方", "期末余额借方", "期末余额贷方"]}]})

    def test_unidentified_business_rows_are_not_sent_as_headers(self):
        self.path.write_text("秘密公司\n张三,李四,王五\n1002,银行存款,123\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "手动填写表头"):
            preview_headers(self.path, "CSV")

    def test_old_provider_preferences_cannot_select_jev(self):
        for provider in ("jev", "cascade", "deepseek"):
            with patch("ledger_viewer.ui.deepseek_get_api_key", return_value="deepseek"), \
                 patch("ledger_viewer.ui.get_api_key") as get_jev:
                label, suggest, key = ai_tools(SimpleNamespace(ai_provider=provider))
                self.assertEqual(label, "DeepSeek Flash")
                self.assertEqual(key(), "deepseek")
                get_jev.assert_not_called()

    def test_import_pending_rows_never_trigger_cloud_triage(self):
        single = SimpleNamespace(_apply_resolution=unittest.mock.Mock())
        with patch("ledger_viewer.row_triage.triage_rows") as cloud:
            ImportDialog._auto_resolve(single, "spec", "result")
            cloud.assert_not_called()
        single._apply_resolution.assert_called_once_with("spec", "result", None)

    def test_provider_migration_ignores_saved_jev_strategy(self):
        base = Path(self.temp.name)
        (base / ".dudledger").mkdir()
        (base / ".dudledger" / "preferences.json").write_text('{"import_strategy":"jev"}')
        with patch("ledger_viewer.ui.Path.home", return_value=base):
            root = Application()
        try:
            self.assertEqual(root.ai_provider, "deepseek")
            self.assertEqual(root.preferences["import_strategy"], "deepseek")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
