import threading
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from ledger_viewer.importer import ImportSpec
from ledger_viewer.jev_mapping import MappingSuggestion
from ledger_viewer.ui import ImportDialog
from tests.import_ui_helpers import ImportUiCase


class ImportDialogTest(ImportUiCase):
    def test_manual_mode_uses_local_rules_without_calling_ai(self):
        dialog = self.dialog(ai=False)
        self.wait_review(dialog)
        self.assertTrue(dialog.local_mode)
        self.suggest.assert_not_called()
        self.assertTrue(dialog.reviews[0].ready)
        self.assertEqual(self.window.store.sources(), [])

    def test_file_selection_automatically_reviews_without_writing_and_confirmation_reuses_result(self):
        dialog = self.dialog()
        self.wait_review(dialog)
        self.suggest.assert_called_once()
        self.assertEqual(self.window.store.sources(), [])
        self.assertEqual(dialog.import_button['text'], '确认导入')
        self.assertEqual(str(dialog.import_button['state']), 'normal')
        detail = dialog.detail.get('1.0', 'end')
        for text in ('表头第 1 行', '借方金额 ← 第 3 列', '拟导入 1 行', '10.00'):
            self.assertIn(text, detail)
        with patch('ledger_viewer.ui.messagebox.showinfo'), patch('ledger_viewer.import_work.precheck') as precheck:
            dialog.import_button.invoke()
            self.wait_for(lambda: not dialog.winfo_exists())
        precheck.assert_not_called()
        self.suggest.assert_called_once()
        self.assertEqual(len(self.window.store.sources()), 1)

    def test_unknown_labels_can_be_suggested_without_opening_editor(self):
        source = self.csv('custom.csv', '编号,名称,增加,减少')
        dialog = self.dialog([source])
        self.wait_review(dialog)
        self.assertTrue(dialog.reviews[0].ready)
        self.assertEqual(dialog.items[0][2].mapping['credit'], 3)

    def test_multi_sheet_workbook_reviews_every_sheet_and_waits_for_confirmation(self):
        path = self.base / 'multi.xlsx'
        workbook = Workbook()
        for sheet in (workbook.active, workbook.create_sheet('第二张')):
            sheet.append(['科目编码', '科目名称', '借方金额', '贷方金额'])
            sheet.append(['1002', '银行存款', 10, 0])
        workbook.save(path)
        dialog = self.dialog([path])
        self.wait_review(dialog)
        self.assertEqual(self.suggest.call_count, 2)
        self.assertEqual(dialog.selected, {0, 1})
        self.assertEqual(self.window.store.sources(), [])
        with patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog._import_selected()
            self.wait_for(lambda: not dialog.winfo_exists())
        self.assertEqual(len(self.window.store.sources()), 2)

    def test_any_failed_sheet_blocks_entire_batch_until_unchecked(self):
        other = self.csv('other.csv')
        good = self.suggestion({'rows': [{'row': 1, 'cells': ['科目编码', '科目名称', '借方金额', '贷方金额']}]}, '')
        self.suggest.side_effect = [good, ValueError('无法识别这张表')]
        dialog = self.dialog([self.source, other])
        self.wait_review(dialog)
        self.assertTrue(dialog.reviews[0].ready)
        self.assertIn('无法识别', dialog.review_errors[1])
        self.assertEqual(str(dialog.import_button['state']), 'disabled')
        dialog._import_selected()
        self.assertEqual(self.window.store.sources(), [])
        self.root.update_idletasks()
        x, y, width, height = dialog.view.bbox('1', 'selected')
        dialog._toggle_item(SimpleNamespace(x=x + 10, y=y + height // 2))
        self.assertEqual(dialog.selected, {0})
        self.assertEqual(str(dialog.import_button['state']), 'normal')
        with patch('ledger_viewer.ui.messagebox.showinfo'):
            dialog._import_selected()
            self.wait_for(lambda: not dialog.winfo_exists())
        self.assertEqual(len(self.window.store.sources()), 1)
        self.assertEqual(self.suggest.call_count, 2)

    def test_model_warning_cannot_be_bypassed_by_clicking_import_again(self):
        good = self.suggestion({'rows': [{'row': 1, 'cells': ['科目编码', '科目名称', '借方金额', '贷方金额']}]}, '')
        self.suggest.side_effect = None
        self.suggest.return_value = MappingSuggestion(good.kind, good.header_row, good.mapping, {}, ('借贷列有未解决歧义',), 'fake')
        dialog = self.dialog()
        self.wait_review(dialog)
        self.assertIn('歧义', dialog.reviews[0].problem)
        dialog._import_selected()
        self.assertEqual(self.window.store.sources(), [])
        self.suggest.assert_called_once()

    def test_notices_are_shown_without_blocking(self):
        good = self.suggestion({'rows': [{'row': 1, 'cells': ['科目编码', '科目名称', '借方金额', '贷方金额']}]}, '')
        self.suggest.side_effect = None
        self.suggest.return_value = MappingSuggestion(good.kind, good.header_row, good.mapping, {}, (), 'fake', ('已忽略原币列',))
        dialog = self.dialog()
        self.wait_review(dialog)
        self.assertEqual(str(dialog.import_button['state']), 'normal')
        self.assertIn('已忽略原币列', dialog.detail.get('1.0', 'end'))

    def test_manual_mapping_is_preserved_and_only_prechecked_locally(self):
        dialog = self.dialog()
        self.wait_review(dialog)
        dialog._edit_current()
        editor = dialog._editor
        self.root.update()
        editor.vars['debit'].set(editor.columns[3])
        editor.vars['credit'].set(editor.columns[2])
        editor.import_now()
        self.wait_review(dialog)
        self.assertIn(0, dialog.manual)
        self.assertEqual(dialog.reviews[0].spec.mapping['credit'], 2)
        self.suggest.assert_called_once()
        dialog._edit_current()
        editor = dialog._editor
        self.assertEqual(editor.vars['credit'].get(), editor.columns[2])
        editor.destroy()
        self.wait_review(dialog)
        self.suggest.assert_called_once()

    def test_reselecting_a_reviewed_sheet_does_not_repeat_ai(self):
        dialog = self.dialog()
        self.wait_review(dialog)
        self.root.update_idletasks()
        x, y, width, height = dialog.view.bbox('0', 'selected')
        event = SimpleNamespace(x=x + 10, y=y + height // 2)
        dialog._toggle_item(event)
        self.assertEqual(str(dialog.import_button['state']), 'disabled')
        dialog._toggle_item(event)
        self.assertEqual(str(dialog.import_button['state']), 'normal')
        self.suggest.assert_called_once()

    def test_changed_file_invalidates_all_cached_sheets_before_any_write(self):
        other = self.csv('other.csv')
        dialog = self.dialog([self.source, other])
        self.wait_review(dialog)
        self.source.write_text(self.source.read_text().replace('10,0', '20,0'))
        dialog._import_selected()
        self.wait_for(lambda: not dialog.working)
        self.assertIn('原文件已修改', dialog.review_errors[0])
        self.assertIn(1, dialog.reviews)
        self.assertEqual(str(dialog.import_button['state']), 'disabled')
        self.assertEqual(self.window.store.sources(), [])
        dialog._retry_review()
        self.wait_review(dialog)
        self.assertEqual(dialog.reviews[0].report.stats.debit_cents, 2000)
        self.assertEqual(self.window.store.sources(), [])

    def test_batch_and_editor_use_same_header_detection_even_with_stale_local_spec(self):
        self.source.write_text('标题\n科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n')
        stale = ImportSpec(self.source, 'CSV', 1, 'entry', {'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3})
        with patch('ledger_viewer.ui.inventory', return_value=([(self.source, 'CSV', stale)], [], {})):
            dialog = self.dialog()
        self.wait_review(dialog)
        self.assertEqual(dialog.reviews[0].spec.header_row, 2)
        dialog._edit_current()
        editor = dialog._editor
        editor._offer_ai()
        self.wait_for(lambda: not editor.tasks.busy('review_ai'))
        self.assertEqual(editor.header.get(), '2')
        self.assertEqual(editor._ai_review.spec.mapping, dialog.reviews[0].spec.mapping)
        self.assertEqual([call.args[0] for call in self.suggest.call_args_list][0], self.suggest.call_args_list[1].args[0])
        editor.destroy()

    def test_manual_mode_cancels_ai_and_discards_late_result(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.suggestion
        def slow(preview, key):
            started.set()
            release.wait(3)
            finished.set()
            return original(preview, key)
        self.suggest.side_effect = slow
        dialog = self.dialog()
        self.wait_for(started.is_set)
        dialog._use_local_mode()
        release.set()
        self.wait_review(dialog)
        self.wait_for(finished.is_set)
        self.assertTrue(dialog.local_mode)
        self.assertTrue(dialog.reviews[0].ready)
        self.assertEqual(self.window.store.sources(), [])
        self.assertFalse(self.window.import_busy)

    def test_large_font_keeps_actions_and_long_error_readable(self):
        self.root.set_font_size(20, persist=False)
        self.suggest.side_effect = ValueError('需要进一步核对。' * 200)
        dialog = self.dialog()
        self.wait_review(dialog)
        self.root.update_idletasks()
        for widget in (dialog.import_button, dialog.agent_button, dialog.edit_button, dialog.retry_button, dialog.settings_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
        self.assertIn('需要进一步核对。' * 200, dialog.detail.get('1.0', 'end'))
        self.assertEqual(len([c for c in dialog.winfo_children() if isinstance(c, ImportDialog)]), 0)

    def test_single_editor_ai_action_is_visible_at_large_font(self):
        self.root.set_font_size(20, persist=False)
        editor = ImportDialog(self.window, self.source)
        self.root.update()
        self.assertTrue(editor.ai_button.winfo_ismapped())
        self.assertLess(editor.ai_button.winfo_rooty(), editor.map_canvas.winfo_rooty())
        editor.destroy()

    def test_stop_during_fingerprint_check_prevents_any_write(self):
        dialog = self.dialog()
        self.wait_review(dialog)
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        from ledger_viewer.importer import file_hash
        def slow_hash(path):
            started.set()
            release.wait(3)
            result = file_hash(path)
            finished.set()
            return result
        with patch('ledger_viewer.ui.file_hash', side_effect=slow_hash):
            dialog._import_selected()
            self.wait_for(started.is_set)
            dialog._close()
            release.set()
            self.wait_for(finished.is_set)
        self.assertFalse(dialog.working)
        self.assertTrue(dialog.winfo_exists())
        self.assertIn('已停止', dialog.ai_status.get())
        self.assertEqual(self.window.store.sources(), [])
