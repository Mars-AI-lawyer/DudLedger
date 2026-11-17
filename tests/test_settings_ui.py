import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

import tkinter as tk

from ledger_viewer.ui import Application, SettingsDialog
from tkinter import ttk


class SettingsDialogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_ai_settings_can_save_all_keys(self):
        saved = {'jev': '', 'deepseek': '', 'bailian': ''}
        with ExitStack() as stack:
            for provider, getter, setter in [('jev', 'get_api_key', 'save_api_key'),
                    ('deepseek', 'deepseek_get_api_key', 'deepseek_save_api_key'),
                    ('bailian', 'bailian_get_api_key', 'bailian_save_api_key')]:
                stack.enter_context(patch('ledger_viewer.ui.' + getter, side_effect=lambda p=provider: saved[p]))
                stack.enter_context(patch('ledger_viewer.ui.' + setter, side_effect=lambda value, p=provider: saved.__setitem__(p, value)))
            app = Application()
            self.addCleanup(app.destroy)
            app.preferences_path = self.base / 'preferences.json'
            app.preferences = {}
            dialog = SettingsDialog(next(iter(app.windows)), ai=True)
            app.update()
            self.assertEqual(dialog.tabs.select(), str(dialog.ai_tab))
            self.assertIn('DeepSeek', dialog.provider_tabs.tab(dialog.provider_tabs.select(), 'text'))
            self.assertLess(dialog.winfo_reqheight(), app.winfo_screenheight() - 32)
            self.assertFalse(dialog.bailian_free_confirmed.get())
            with patch('ledger_viewer.jev_ui.launch_connection_test') as network:
                dialog._test_bailian_connection()
                network.assert_not_called()
            dialog.api_key.set('fictional-jev')
            dialog.deepseek_key.set('fictional-deepseek')
            dialog.bailian_key.set('fictional-bailian')
            dialog.bailian_free_confirmed.set(True)
            dialog._finish()
            self.assertEqual(saved, {'jev': 'fictional-jev', 'deepseek': 'fictional-deepseek', 'bailian': 'fictional-bailian'})
            self.assertTrue(app.preferences['bailian_free_confirmed'])
            self.assertNotIn('fictional-', app.preferences_path.read_text())

    def test_first_run_can_be_skipped_without_saving_credentials(self):
        with patch('ledger_viewer.ui.get_api_key', return_value=''), \
             patch('ledger_viewer.ui.deepseek_get_api_key', return_value=''), \
             patch('ledger_viewer.ui.bailian_get_api_key', return_value=''), \
             patch('ledger_viewer.ui.save_api_key') as save_jev, \
             patch('ledger_viewer.ui.deepseek_save_api_key') as save_deepseek, \
             patch('ledger_viewer.ui.bailian_save_api_key') as save_bailian:
            app = Application()
            self.addCleanup(app.destroy)
            app.preferences_path = self.base / 'preferences.json'
            app.preferences = {}
            dialog = app.offer_ai_setup()
            dialog.skip()
            for save in (save_jev, save_deepseek, save_bailian):
                save.assert_not_called()
            self.assertIsNone(app.offer_ai_setup())

    def test_checkboxes_use_ticks_and_location_setting_can_restore_source_folder(self):
        app = Application()
        self.addCleanup(app.destroy)
        app.preferences_path = self.base / 'preferences.json'
        app.preferences = {'book_location_mode': 'fixed', 'book_folder': str(self.base / 'books')}
        self.assertEqual(ttk.Style(app).layout('TCheckbutton'), ttk.Style(app).layout('Sampling.TCheckbutton'))
        dialog = SettingsDialog(next(iter(app.windows)))
        dialog.book_location_mode.set('source')
        dialog._save_book_location()
        self.assertEqual(app.import_book_folder(self.base / 'source.xlsx'), self.base.resolve())
        self.assertEqual(app.preferences['book_location_mode'], 'source')
        dialog.destroy()

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
        ), patch("ledger_viewer.ui.deepseek_memory_only", return_value=False), patch(
            # 不 mock 的话，这里会删掉系统钥匙串里用户真实的 DeepSeek 密钥
            "ledger_viewer.ui.deepseek_delete_api_key", side_effect=lambda: saved.__setitem__("key", "")
        ):
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

    def test_import_provider_is_fixed_to_deepseek(self):
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
                self.assertEqual(settings.provider.get(), "DeepSeek Flash")
                settings.provider.set("DeepSeek")
                settings._save_provider()
                self.assertEqual(app.ai_provider, "deepseek")
                self.assertIn("deepseek", app.preferences_path.read_text(encoding="utf-8"))
                reopened = SettingsDialog(window)
                self.assertEqual(reopened.provider.get(), "DeepSeek Flash")
                reopened.destroy()
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
