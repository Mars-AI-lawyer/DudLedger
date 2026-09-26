import json
import unittest
from unittest.mock import patch

from ledger_viewer import deepseek_mapping
from ledger_viewer.deepseek_mapping import suggest_mapping


PREVIEW = {
    "sheet": "凭证",
    "rows": [
        {"row": 1, "cells": ["日期", "凭证号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额"]},
        {"row": 2, "cells": ["2026-08-01", "记-1", "1002", "银行存款", "收款", "10", "0"]},
        {"row": 3, "cells": ["2026-08-02", "记-2", "6001", "主营业务收入", "销售", "0", "200"]},
    ],
}


def canned(answer, raw=False):
    calls = []

    def chat(api_key, payload):
        assert api_key == "sk-test"
        calls.append(payload)
        return answer if raw else json.dumps(answer, ensure_ascii=False)

    chat.calls = calls
    return chat


class DeepSeekMappingTest(unittest.TestCase):
    def test_payload_disables_thinking_and_result_is_parsed(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"date": 1, "voucher_no": 2, "account_code": 3, "account_name": 4, "summary": 5, "debit": 6, "credit": 7},
                       "warnings": ["请核对借贷方向"]})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        payload = chat.calls[0]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertFalse(payload["stream"])
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertEqual(result.kind, "entry")
        self.assertEqual(result.header_row, 1)
        self.assertEqual(result.mapping, {"date": 0, "voucher_no": 1, "account_code": 2, "account_name": 3, "summary": 4, "debit": 5, "credit": 6})
        self.assertEqual(result.warnings, ("请核对借贷方向",))
        self.assertEqual(result.model, "deepseek-flash")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned({"kind": "unknown", "header_row": 1, "mapping": {}}))

    def test_header_row_must_exist_in_preview(self):
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned({"kind": "entry", "header_row": 9, "mapping": {}}))

    def test_duplicate_and_out_of_range_columns_are_dropped_with_warnings(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"account_code": 7, "account_name": 7, "credit": 3, "debit": 99},
                       "warnings": []})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        self.assertEqual(result.mapping, {"account_code": 6, "credit": 2})
        self.assertEqual(len(result.warnings), 2)

    def test_missing_required_fields_add_import_warning(self):
        chat = canned({"kind": "entry", "header_row": 1, "mapping": {"summary": 5}, "warnings": []})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        self.assertEqual(result.mapping, {"summary": 4})
        self.assertTrue(any("尚不满足导入条件" in warning for warning in result.warnings))

    def test_non_json_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned("抱歉，我无法判断这张表。", raw=True))
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned("结果：{不是合法的json}", raw=True))


class DeepSeekKeyTest(unittest.TestCase):
    def setUp(self):
        deepseek_mapping._SESSION_KEY = None
        deepseek_mapping._MEMORY_ONLY = False

    def tearDown(self):
        deepseek_mapping._SESSION_KEY = None
        deepseek_mapping._MEMORY_ONLY = False

    def test_secure_backend_roundtrip(self):
        with patch.object(deepseek_mapping.keyring, "set_password") as set_pw, \
             patch.object(deepseek_mapping.keyring, "get_password", return_value="sk-saved"), \
             patch.object(deepseek_mapping.keyring, "delete_password") as del_pw, \
             patch.object(deepseek_mapping, "_secure_backend", return_value=True):
            deepseek_mapping.save_api_key("sk-saved")
            set_pw.assert_called_once_with(deepseek_mapping.KEYRING_SERVICE, deepseek_mapping.KEYRING_ACCOUNT, "sk-saved")
            self.assertEqual(deepseek_mapping.get_api_key(), "sk-saved")
            self.assertFalse(deepseek_mapping.key_memory_only())
            deepseek_mapping.delete_api_key()
            del_pw.assert_called_once()

    def test_insecure_backend_keeps_key_in_memory_only(self):
        with patch.object(deepseek_mapping, "_secure_backend", return_value=False):
            deepseek_mapping.save_api_key("sk-memory")
            self.assertTrue(deepseek_mapping.key_memory_only())
            self.assertEqual(deepseek_mapping.get_api_key(), "sk-memory")
            deepseek_mapping.delete_api_key()
            self.assertEqual(deepseek_mapping.get_api_key(), "")

    def test_invalid_key_text_is_rejected(self):
        with self.assertRaises(ValueError):
            deepseek_mapping.save_api_key("带空格的 key")


if __name__ == "__main__":
    unittest.main()
