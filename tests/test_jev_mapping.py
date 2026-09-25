import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ledger_viewer.jev_mapping import (
    PREVIEW_COLUMNS, PREVIEW_ROWS, delete_api_key, get_api_key,
    preview_sheet, save_api_key, suggest_mapping,
)


class FakeClient:
    def __init__(self, *, api_key, model, timeout):
        assert api_key == "test-key"
        assert model == "jev-latest"
        assert timeout > 0
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def system_one(self, *, state, questions):
        self.calls.append((state, questions))
        if "kind" in questions:
            choices = {"kind": ("balance", 0.95), "header": ("r3", 0.89)}
        else:
            choices = {
                "account_code": ("c1", 0.95), "account_name": ("c2", 0.95),
                "opening_debit": ("c3", 0.85), "opening_credit": ("c4", 0.85),
                "period_debit": ("c5", 0.8), "period_credit": ("c6", 0.8),
                "year_debit": ("c7", 0.7), "year_credit": ("c8", 0.7),
                "closing_debit": ("c9", 0.95), "closing_credit": ("c10", 0.95),
            }
            choices = {field: choices.get(field, ("none", 0.9)) for field in questions}
        return SimpleNamespace(
            choices={name: SimpleNamespace(choice=value, confidence=score) for name, (value, score) in choices.items()},
            model="jev-1.13.0",
        )


class JevMappingTest(unittest.TestCase):
    def test_preview_is_bounded_and_suggestion_never_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "balance.csv"
            source.write_text(
                "说明\n"
                "科目编码,科目名称,期初余额,,本期发生,,本年累计,,期末余额,\n"
                ",,借方,贷方,借方,贷方,借方,贷方,借方,贷方\n"
                "1002,银行,0,0,100,0,100,0,100,0\n",
                encoding="utf-8",
            )
            preview = preview_sheet(source, "CSV")
            self.assertLessEqual(len(preview["rows"]), PREVIEW_ROWS)
            self.assertLessEqual(max(len(row["cells"]) for row in preview["rows"]), PREVIEW_COLUMNS)
            clients = []
            def factory(**options):
                client = FakeClient(**options)
                clients.append(client)
                return client
            suggestion = suggest_mapping(preview, "test-key", factory)
            self.assertEqual((suggestion.kind, suggestion.header_row), ("balance", 3))
            self.assertEqual(suggestion.mapping["period_debit"], 4)
            self.assertEqual(suggestion.mapping["closing_credit"], 9)
            self.assertEqual(len(clients[0].calls), 2)
            self.assertEqual(source.read_text(encoding="utf-8").splitlines()[0], "说明")

    def test_unknown_kind_does_not_propose_columns(self):
        class UnknownClient(FakeClient):
            def system_one(self, *, state, questions):
                return SimpleNamespace(choices={
                    "kind": SimpleNamespace(choice="unknown", confidence=0.8),
                    "header": SimpleNamespace(choice="r1", confidence=0.8),
                })
        with self.assertRaisesRegex(ValueError, "无法可靠确认"):
            suggest_mapping({"sheet": "CSV", "rows": [{"row": 1, "cells": ["标题"]}]}, "test-key", UnknownClient)

    def test_key_is_delegated_to_system_keyring(self):
        with patch("ledger_viewer.jev_mapping.keyring") as keyring:
            keyring.get_password.return_value = "test-key"
            self.assertEqual(get_api_key(), "test-key")
            save_api_key("  test-key  ")
            keyring.set_password.assert_called_once_with("DudLedger Jev", "typesafe_api_key", "test-key")
            delete_api_key()
            keyring.delete_password.assert_called_once()
            with self.assertRaises(ValueError):
                save_api_key("bad key")

    def test_duplicate_amount_column_is_left_for_manual_review(self):
        class DuplicateClient(FakeClient):
            def system_one(self, *, state, questions):
                if "kind" in questions:
                    proposed = {"kind": ("balance_sheet", 0.9), "header": ("r1", 0.9)}
                else:
                    proposed = {field: ("none", 0.9) for field in questions}
                    proposed.update({
                        "left_item": ("c1", 0.9), "left_amount_1": ("c2", 0.9),
                        "left_amount_2": ("c3", 0.9), "left_amount_3": ("c3", 0.7),
                        "right_item": ("c4", 0.9), "right_amount_1": ("c5", 0.9),
                        "right_amount_2": ("c6", 0.9), "right_amount_3": ("c6", 0.7),
                    })
                return SimpleNamespace(choices={
                    field: SimpleNamespace(choice=value, confidence=score)
                    for field, (value, score) in proposed.items()
                }, model="jev-1.13.0")

        preview = {"sheet": "资产负债表", "rows": [
            {"row": 1, "cells": ["资产", "期末余额", "上年年末余额", "负债", "期末余额", "上年年末余额"]},
            {"row": 2, "cells": ["货币资金", "100", "80", "短期借款", "50", "60"]},
        ]}
        suggestion = suggest_mapping(preview, "test-key", DuplicateClient)
        self.assertNotIn("left_amount_3", suggestion.mapping)
        self.assertNotIn("right_amount_3", suggestion.mapping)
        self.assertIn("左侧项目右边第三列金额", " ".join(suggestion.warnings))


if __name__ == "__main__":
    unittest.main()
