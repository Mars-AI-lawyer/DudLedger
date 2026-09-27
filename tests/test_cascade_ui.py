import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.jev_mapping import MappingSuggestion
from ledger_viewer.ui import Application, BatchImportDialog, copy_agent_task


class CascadeUiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "账.csv"
        self.source.write_text("日期,凭证号,科目编码,科目名称,金额,借贷方向\n"
                               "2026-09-01,记1,1002,银行存款,10,借\n", encoding="utf-8")
        try:
            with patch("ledger_viewer.ui.Path.home", return_value=self.base):
                self.app = Application()
        except tk.TclError as exc:
            self.temp.cleanup()
            self.skipTest(str(exc))
        self.assertEqual(self.app.ai_provider, "cascade")
        self.window = next(iter(self.app.windows))
        self.window._use_book(self.base / "existing.sqlite")
        self.dialog = BatchImportDialog(self.window, [self.source])
        self.good = MappingSuggestion("entry", 1, {"date": 0, "voucher_no": 1, "account_code": 2,
                                       "account_name": 3, "amount": 4, "direction": 5}, {}, (), "test")

    def tearDown(self):
        self.window._close()
        self.temp.cleanup()

    def wait_done(self):
        deadline = time.monotonic() + 5
        while self.dialog.winfo_exists() and self.dialog.ai_running and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

    def test_escalation_imports_with_single_confirmation(self):
        with patch("ledger_viewer.ui.get_api_key", return_value="first"), \
             patch("ledger_viewer.ui.deepseek_get_api_key", return_value="second"), \
             patch("ledger_viewer.ui.suggest_mapping", side_effect=ValueError("无法识别")) as first, \
             patch("ledger_viewer.ui.deepseek_suggest_mapping", return_value=self.good) as second, \
             patch("ledger_viewer.ui.messagebox.askokcancel", return_value=True) as consent, \
             patch("ledger_viewer.ui.messagebox.showinfo"):
            self.dialog._import_selected()
            self.wait_done()
        consent.assert_called_once()
        self.assertIn("Jev", consent.call_args.args[1])
        self.assertIn("DeepSeek", consent.call_args.args[1])
        first.assert_called_once()
        second.assert_called_once()
        self.assertEqual(len(self.window.store.sources()), 1)

    def test_cancel_consent_sends_nothing(self):
        with patch("ledger_viewer.ui.get_api_key", return_value="first"), \
             patch("ledger_viewer.ui.deepseek_get_api_key", return_value="second"), \
             patch("ledger_viewer.ui.suggest_mapping") as first, \
             patch("ledger_viewer.ui.deepseek_suggest_mapping") as second, \
             patch("ledger_viewer.ui.messagebox.askokcancel", return_value=False):
            self.dialog._import_selected()
        first.assert_not_called()
        second.assert_not_called()
        self.assertEqual(self.window.store.sources(), [])

    def test_two_failures_leave_book_empty_and_enable_handoff(self):
        with patch("ledger_viewer.ui.get_api_key", return_value="first"), \
             patch("ledger_viewer.ui.deepseek_get_api_key", return_value="second"), \
             patch("ledger_viewer.ui.suggest_mapping", side_effect=ValueError("无法识别")), \
             patch("ledger_viewer.ui.deepseek_suggest_mapping", side_effect=ValueError("仍无法识别")), \
             patch("ledger_viewer.ui.messagebox.askokcancel", return_value=True), \
             patch("ledger_viewer.ui.messagebox.showwarning") as warning:
            self.dialog._import_selected()
            self.wait_done()
        warning.assert_called_once()
        self.assertEqual(self.window.store.sources(), [])
        self.assertEqual(str(self.dialog.agent_button.cget("state")), "normal")
        # Intercept clipboard calls so tests do not replace the user's clipboard.
        with patch.object(self.dialog, "clipboard_clear"), patch.object(self.dialog, "clipboard_append") as copied, \
             patch("ledger_viewer.ui.messagebox.showinfo"):
            copy_agent_task(self.dialog, [(self.source, "CSV")])
        self.assertIn(str(self.source.resolve()), copied.call_args.args[0])

    def test_closing_batch_sets_cancellation_event(self):
        self.dialog._close()
        self.assertTrue(self.dialog.ai_cancel.is_set())
