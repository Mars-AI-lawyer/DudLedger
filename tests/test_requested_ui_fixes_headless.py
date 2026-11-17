"""Exercise import and settings behavior with no native Tk window or real network."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer.importer import guess_mapping
from ledger_viewer.jev_mapping import MappingSuggestion
from ledger_viewer.review_providers import order_description, provider_order
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import BatchImportDialog, SettingsDialog


class Variable:
    def __init__(self, *args, value='', **kwargs):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class InlineTasks:
    """Complete the real import review synchronously behind fake display widgets."""
    def cancel(self, *_args):
        pass

    def busy(self, *_args):
        return False

    def submit(self, key, work, done, error, progress):
        from threading import Event
        try:
            result = work(Event(), progress)
        except Exception as exc:
            error(exc)
        else:
            done(result)


class ImportDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.path = base / 'fictional.csv'
        self.path.write_text('科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n', encoding='utf-8')
        self.store = LedgerStore(base / 'book.sqlite')
        self.addCleanup(self.store.close)
        self.parent = SimpleNamespace(store=self.store, controller=SimpleNamespace(preferences={}), import_busy=False)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch('ledger_viewer.ui.tk.Toplevel.__init__', return_value=None))
        for name in ('title', 'geometry', 'minsize', 'transient', 'grab_set', 'protocol', 'bind', 'after_idle'):
            stack.enter_context(patch.object(BatchImportDialog, name))
        for name, value in (('winfo_exists', True), ('winfo_screenwidth', 1440), ('winfo_screenheight', 900)):
            stack.enter_context(patch.object(BatchImportDialog, name, return_value=value))
        for name in ('Frame', 'Label', 'Progressbar', 'Button', 'Menubutton', 'LabelFrame', 'Scrollbar'):
            stack.enter_context(patch('ledger_viewer.ui.ttk.' + name))
        for name in ('StringVar', 'BooleanVar'):
            stack.enter_context(patch('ledger_viewer.ui.tk.' + name, Variable))
        stack.enter_context(patch('ledger_viewer.ui.tk.Text'))
        stack.enter_context(patch('ledger_viewer.ui.tk.Menu'))
        stack.enter_context(patch('ledger_viewer.ui.tree', return_value=Mock()))
        stack.enter_context(patch('ledger_viewer.ui.BackgroundTasks', return_value=InlineTasks()))
        stack.enter_context(patch.object(BatchImportDialog, '_show_sheet_status'))
        self.key = stack.enter_context(patch('ledger_viewer.ui.deepseek_get_api_key', return_value='fictional-key'))
        self.suggest = stack.enter_context(patch('ledger_viewer.ui.deepseek_suggest_mapping', side_effect=self.suggestion))
        self.confirm = stack.enter_context(patch('ledger_viewer.ui.messagebox.askyesno'))
        self.dialog = BatchImportDialog(self.parent, [self.path])

    @staticmethod
    def suggestion(preview, key):
        row = preview['rows'][0]
        return MappingSuggestion('entry', row['row'], guess_mapping(row['cells'], 'entry'), {}, (), 'fake-model')

    def test_default_auto_review_is_ready_without_manual_mapping_or_early_write(self):
        self.assertFalse(self.dialog.local_mode)
        self.dialog._start_review()
        self.suggest.assert_called_once()
        self.assertTrue(self.dialog.reviews[0].ready)
        self.assertEqual(self.store.sources(), [])
        self.confirm.assert_not_called()

    def test_manual_mode_runs_locally_then_can_resume_ai(self):
        self.dialog._use_local_mode()
        self.assertTrue(self.dialog.local_mode)
        self.suggest.assert_not_called()
        self.assertTrue(self.dialog.reviews[0].ready)
        self.dialog._use_ai_mode()
        self.assertFalse(self.dialog.local_mode)
        self.suggest.assert_called_once()
        self.assertTrue(self.dialog.reviews[0].ready)
        self.confirm.assert_not_called()

    def test_missing_key_shows_setup_or_manual_path_and_does_not_send(self):
        self.key.return_value = ''
        self.dialog._start_review()
        self.suggest.assert_not_called()
        self.assertIn('手动设置', self.dialog.review_errors[0])
        self.assertTrue(self.dialog._needs_key)
        self.assertFalse(self.dialog.reviews)
        self.dialog._use_local_mode()
        self.assertTrue(self.dialog.reviews[0].ready)
        self.suggest.assert_not_called()

    def test_ai_failure_keeps_import_blocked_until_manual_correction(self):
        self.suggest.side_effect = ValueError('列识别失败')
        self.dialog._start_review()
        self.assertIn('列识别失败', self.dialog.review_errors[0])
        self.assertEqual(self.store.sources(), [])
        self.dialog._use_local_mode()
        self.assertTrue(self.dialog.reviews[0].ready)

    def test_explicit_manual_mapping_is_preserved_when_ai_is_enabled(self):
        self.dialog._use_local_mode()
        reviewed = self.dialog.reviews[0]
        self.dialog.manual.add(0)
        self.dialog._use_ai_mode()
        self.assertIs(self.dialog.reviews[0], reviewed)
        self.suggest.assert_not_called()


class ProviderSettingsTest(unittest.TestCase):
    def test_model_preference_is_saved_and_part_of_unsaved_changes(self):
        parent = SimpleNamespace(controller=SimpleNamespace(preferences={}, _save_preferences=Mock()))
        dialog = SimpleNamespace(parent=parent, shortcut_vars={}, api_key=Variable(), deepseek_key=Variable(),
            bailian_key=Variable(), bailian_endpoint=Variable(value='https://dashscope.aliyuncs.com/compatible-mode/v1/systemone'),
            bailian_free_confirmed=Variable(value=False), review_preferred=Variable(value='百炼优先（默认）'),
            _stop_shortcut_recording=Mock(), destroy=Mock())
        dialog._deferred_state = lambda: SettingsDialog._deferred_state(dialog)
        before = dialog._deferred_state()
        dialog.review_preferred.set('Jev 优先')
        self.assertNotEqual(before, dialog._deferred_state())
        with patch('ledger_viewer.ui.validate_shortcuts', return_value={}), \
             patch('ledger_viewer.ui.get_api_key', return_value=''), \
             patch('ledger_viewer.ui.deepseek_get_api_key', return_value=''), \
             patch('ledger_viewer.ui.bailian_get_api_key', return_value=''):
            SettingsDialog._finish(dialog)
        self.assertEqual(parent.controller.preferences['review_preferred_provider'], 'jev')
        parent.controller._save_preferences.assert_called_once()
        dialog.destroy.assert_called_once()

    def test_preview_description_matches_runtime_fallback_order(self):
        for preference in ('bailian', 'jev', None, 'obsolete', [], {}):
            for enabled in (False, True):
                order = provider_order(preference, bailian_enabled=enabled)
                text = order_description(preference, bailian_enabled=enabled)
                self.assertEqual(order[-1], 'deepseek')
                self.assertEqual('bailian' in order, enabled)
                self.assertEqual(order[0], 'jev' if preference == 'jev' or not enabled else 'bailian')
                self.assertTrue(text.startswith('Jev' if order[0] == 'jev' else '百炼'))


if __name__ == '__main__':
    unittest.main()
