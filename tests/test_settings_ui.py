import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tkinter as tk

from ledger_viewer.ui import Application, SettingsDialog


class SettingsDialogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_finish_saves_typed_key_and_reopening_shows_saved_key(self):
        saved = {"key": ""}
        with patch("ledger_viewer.ui.get_api_key", side_effect=lambda: saved["key"]), patch(
            "ledger_viewer.ui.save_api_key", side_effect=lambda value: saved.__setitem__("key", value)
        ):
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            try:
                window = next(iter(app.windows))
                settings = SettingsDialog(window)
                settings.api_key.set("test-key")
                settings._finish()
                self.assertEqual(saved["key"], "test-key")
                self.assertFalse(settings.winfo_exists())

                reopened = SettingsDialog(window)
                self.assertEqual(reopened.api_key.get(), "test-key")
                self.assertEqual(reopened.key_entry.cget("show"), "•")
                self.assertIn("密钥已保存", reopened.key_status.get())
                reopened.show_key.set(True)
                reopened._toggle_key_visible()
                self.assertEqual(reopened.key_entry.cget("show"), "")
                self.assertEqual(reopened.deepseek_entry.cget("show"), "")
                reopened.show_key.set(False)
                reopened._toggle_key_visible()
                self.assertEqual(reopened.key_entry.cget("show"), "•")
                reopened._finish()
                self.assertEqual(saved["key"], "test-key")
            finally:
                app.destroy()

    def test_deepseek_key_row_saves_and_redisplays(self):
        saved = {"key": ""}
        with patch("ledger_viewer.ui.deepseek_get_api_key", side_effect=lambda: saved["key"]), patch(
            "ledger_viewer.ui.deepseek_save_api_key", side_effect=lambda value: saved.__setitem__("key", value)
        ), patch("ledger_viewer.ui.deepseek_memory_only", return_value=False):
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            try:
                window = next(iter(app.windows))
                settings = SettingsDialog(window)
                self.assertEqual(settings.deepseek_key.get(), "")
                self.assertIn("尚未保存密钥", settings.deepseek_status.get())
                settings.deepseek_key.set("sk-test-123")
                self.assertTrue(settings._save_deepseek_key())
                self.assertEqual(saved["key"], "sk-test-123")
                self.assertEqual(settings.deepseek_key.get(), "sk-test-123")
                self.assertIn("密钥已保存", settings.deepseek_status.get())
                settings._delete_deepseek_key()
                self.assertEqual(settings.deepseek_key.get(), "")
            finally:
                app.destroy()

    def test_provider_choice_is_persisted(self):
        with patch("ledger_viewer.ui.get_api_key", return_value=""), patch(
            "ledger_viewer.ui.deepseek_get_api_key", return_value=""
        ):
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            try:
                app.preferences_path = self.base / "preferences.json"
                app.ai_provider = "jev"
                window = next(iter(app.windows))
                settings = SettingsDialog(window)
                self.assertEqual(settings.provider.get(), "Jev")
                settings.provider.set("DeepSeek")
                settings._save_provider()
                self.assertEqual(app.ai_provider, "deepseek")
                self.assertIn("deepseek", app.preferences_path.read_text(encoding="utf-8"))
                reopened = SettingsDialog(window)
                self.assertEqual(reopened.provider.get(), "DeepSeek")
                reopened.destroy()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
