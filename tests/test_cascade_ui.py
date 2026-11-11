import threading
from unittest.mock import patch

from ledger_viewer.ui import BatchImportDialog
from tests.import_ui_helpers import ImportUiCase


class DeepSeekImportUiTest(ImportUiCase):
    def test_automatic_import_review_never_calls_jev(self):
        with patch('ledger_viewer.jev_mapping.suggest_mapping') as jev, patch('ledger_viewer.ui.messagebox.askokcancel') as consent:
            dialog = self.dialog()
            self.wait_review(dialog)
        jev.assert_not_called()
        consent.assert_not_called()
        self.suggest.assert_called_once()
        self.assertEqual(self.window.store.sources(), [])

    def test_missing_key_shows_settings_and_handoff_without_modal_or_network(self):
        self.key.return_value = ''
        with patch('ledger_viewer.ui.SettingsDialog') as settings:
            dialog = self.dialog()
            self.wait_review(dialog)
        settings.assert_not_called()
        self.suggest.assert_not_called()
        self.assertIn('尚未设置 AI', dialog.detail.get('1.0', 'end'))
        self.assertEqual(str(dialog.settings_button['state']), 'normal')
        self.assertEqual(str(dialog.agent_button['state']), 'normal')
        self.assertEqual(str(dialog.import_button['state']), 'disabled')

    def test_saving_key_from_inline_action_automatically_resumes_review(self):
        self.key.return_value = ''
        dialog = self.dialog()
        self.wait_review(dialog)
        def save_key(parent):
            self.key.return_value = 'saved-fake-key'
            return object()
        with patch('ledger_viewer.ui.SettingsDialog', side_effect=save_key), patch.object(BatchImportDialog, 'wait_window'):
            dialog.settings_button.invoke()
            self.wait_review(dialog)
        self.assertTrue(dialog.reviews[0].ready)
        self.suggest.assert_called_once()
        self.assertEqual(self.window.store.sources(), [])

    def test_failure_is_inline_and_handoff_only_contains_selected_unfinished_sheets(self):
        other = self.csv('other.csv')
        self.suggest.side_effect = ValueError('网络失败 fake-key')
        with patch('ledger_viewer.ui.messagebox.showwarning') as warning:
            dialog = self.dialog([self.source, other])
            self.wait_review(dialog)
        warning.assert_not_called()
        self.assertIn('[密钥已隐藏]', dialog.detail.get('1.0', 'end'))
        self.assertNotIn('fake-key', dialog.detail.get('1.0', 'end'))
        self.assertEqual(dialog.agent_button['style'], 'Accent.TButton')
        dialog.selected = {0, 1}
        dialog.completed = {1}
        with patch.object(dialog, 'clipboard_clear'), patch.object(dialog, 'clipboard_append') as copied, patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog._copy_agent()
        content = copied.call_args.args[0]
        self.assertIn(str(self.source.resolve()), content)
        self.assertNotIn(str(other.resolve()), content)
        self.assertNotIn('fake-key', content)
        self.assertEqual(self.window.store.sources(), [])

    def test_closing_cancels_review_and_drops_late_response(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(preview, key):
            started.set()
            release.wait(3)
            finished.set()
            return self.suggestion(preview, key)
        self.suggest.side_effect = slow
        dialog = self.dialog()
        self.wait_for(started.is_set)
        dialog._close()
        release.set()
        self.wait_for(finished.is_set)
        self.assertTrue(dialog.ai_cancel.is_set())
        self.assertFalse(dialog.winfo_exists())
        self.assertFalse(self.window.import_busy)
        self.assertEqual(self.window.store.sources(), [])
