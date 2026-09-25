import tkinter as tk
import unittest
from unittest.mock import patch

from ledger_viewer.ui import Application, SettingsDialog


class SettingsDialogTest(unittest.TestCase):
    def test_finish_saves_typed_key_and_reopening_shows_saved_status(self):
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
                self.assertEqual(reopened.api_key.get(), "")
                self.assertIn("密钥已保存", reopened.key_status.get())
                reopened.destroy()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
