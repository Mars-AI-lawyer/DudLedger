import json
import unittest
from unittest.mock import patch

from ledger_viewer import deepseek_mapping
from ledger_viewer.deepseek_mapping import suggest_mapping


PREVIEW = {
    "sheet": "凭证",
    "rows": [
        {"row": 1, "cells": ["日期", "凭证号", "科目编码", "科目名称", "摘要", "借方金额", "贷方金额"]},
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
    def test_payload_uses_low_thinking_and_result_is_parsed(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"date": 1, "voucher_no": 2, "account_code": 3, "account_name": 4, "summary": 5, "debit": 6, "credit": 7},
                       "warnings": ["请核对借贷方向"]})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        payload = chat.calls[0]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertFalse(payload["stream"])
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertEqual(result.kind, "entry")
        self.assertEqual(result.header_row, 1)
        self.assertEqual(result.mapping, {"date": 0, "voucher_no": 1, "account_code": 2, "account_name": 3, "summary": 4, "debit": 5, "credit": 6})
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.notices, ("请核对借贷方向",))
        self.assertEqual(result.model, "deepseek-flash")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned({"kind": "unknown", "header_row": 1, "mapping": {}}))

    def test_header_row_must_exist_in_preview(self):
        with self.assertRaises(ValueError):
            suggest_mapping(PREVIEW, "sk-test", chat=canned({"kind": "entry", "header_row": 9, "mapping": {}}))

    def test_duplicate_and_out_of_range_columns_are_reported_with_one_sided_amounts(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"account_code": 7, "account_name": 7, "credit": 3, "debit": 99},
                       "warnings": ["请核对借贷方向"]})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        self.assertEqual(result.mapping, {"account_code": 6, "credit": 2})
        self.assertEqual(result.warnings, ("借方或贷方金额只识别到一侧，请核对另一侧金额列",))
        self.assertEqual(result.notices, (
            "“account_name”与“account_code”建议了同一列，已保留先出现的“account_code”",
            "“debit”建议的第 99 列超出预览范围，已舍弃",
            "请核对借贷方向",
        ))

    def test_model_notes_do_not_block_valid_mapping(self):
        balance_preview = {"sheet": "余额表", "rows": [
            {"row": 1, "cells": [f"列{i}" for i in range(1, 11)]},
        ]}
        chat = canned({"kind": "balance", "header_row": 1,
                       "mapping": {"account_code": 1, "account_name": 2, "opening_debit": 3, "opening_credit": 4,
                                   "period_debit": 5, "period_credit": 6, "year_debit": 7, "year_credit": 8,
                                   "closing_debit": 9, "closing_credit": 10},
                       "warnings": ["表头为两层结构", "请核对借贷方向"]})
        result = suggest_mapping(balance_preview, "sk-test", chat=chat)
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.notices, ("表头为两层结构", "请核对借贷方向"))

    def test_missing_required_fields_add_import_warning(self):
        chat = canned({"kind": "entry", "header_row": 1, "mapping": {"summary": 5}, "warnings": []})
        result = suggest_mapping(PREVIEW, "sk-test", chat=chat)
        self.assertEqual(result.mapping, {"summary": 4})
        self.assertTrue(any("尚不满足导入条件" in warning for warning in result.warnings))

    def test_multirow_preview_is_accepted_and_capped_at_twenty_rows(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"account_name": 4, "debit": 6, "credit": 7}})
        preview = {"sheet": "不另发工作表名", "rows": [*PREVIEW["rows"],
            *({"row": n, "cells": ["业务样本" if n <= 20 else "第二十一行不能发送"]} for n in range(2, 22))]}
        suggest_mapping(preview, "sk-test", chat=chat)
        encoded = json.dumps(chat.calls, ensure_ascii=False)
        self.assertIn('业务样本', encoded)
        self.assertNotIn('第二十一行不能发送', encoded)
        self.assertNotIn('不另发工作表名', encoded)

    def test_payload_strips_extra_metadata_and_enforces_header_limits(self):
        chat = canned({"kind": "entry", "header_row": 1,
                       "mapping": {"account_name": 1, "debit": 2, "credit": 3}})
        preview = {"sheet": "秘密公司", "path": "/private/secret", "rows": [
            {"row": 1, "cells": ["名" * 100] * 60, "raw_data": "秘密明细"}]}
        suggest_mapping(preview, "sk-test", chat=chat)
        payload = json.dumps(chat.calls, ensure_ascii=False)
        for secret in ("秘密公司", "/private/secret", "秘密明细", "名" * 61):
            self.assertNotIn(secret, payload)

    def test_concrete_model_ambiguity_blocks_import_but_notices_are_visible(self):
        result = suggest_mapping(PREVIEW, 'sk-test', chat=canned({'kind': 'entry', 'header_row': 1,
            'mapping': {'account_name': 4, 'debit': 6, 'credit': 7},
            'warnings': ['无法区分本期与本年累计金额'], 'notices': ['已使用本币列']}))
        self.assertEqual(result.warnings, ('无法区分本期与本年累计金额',))
        self.assertEqual(result.notices, ('已使用本币列',))

    def test_multirow_request_preserves_only_numeric_merge_metadata(self):
        preview = {**PREVIEW, 'merged_ranges': [
            {'start_row': 1, 'end_row': 3, 'start_col': 1, 'end_col': 2, 'path': '不要发送'},
            {'start_row': 30, 'end_row': 40, 'start_col': 1, 'end_col': 2},
            {'start_row': '私密文字', 'end_row': 4, 'start_col': 1, 'end_col': 2}]}
        chat = canned({'kind': 'entry', 'header_row': 1,
                       'mapping': {'account_name': 4, 'debit': 6, 'credit': 7}})
        suggest_mapping(preview, 'sk-test', chat=chat)
        encoded = chat.calls[0]['messages'][1]['content']
        self.assertIn('"end_row": 3', encoded)
        self.assertNotIn('不要发送', encoded)
        self.assertNotIn('私密文字', encoded)
        self.assertNotIn('"start_row": 30', encoded)

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
