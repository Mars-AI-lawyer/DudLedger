"""UI checks limited to the AI sampling page and its prompt settings."""
import json
import tkinter as tk
import unittest
from tkinter import ttk
from unittest.mock import patch

import test_jev_ui as legacy_ui
from test_specialist_review import decision_answer
from ledger_viewer.jev_review import ReviewJob, prepare_batch, batch_counts, batch_info
from ledger_viewer.jev_ui import ConsentDialog, ReviewDetail, ReviewRulesDialog
from ledger_viewer.specialist_rules import ACCOUNTS, load_rules


class SpecialistUITest(unittest.TestCase):
    setUp = legacy_ui.ReviewUITest.setUp
    tearDown = legacy_ui.ReviewUITest.tearDown

    def test_account_selection_defaults_and_only_selected_scope(self):
        self.app.account = 'C:1002'
        self.app.search.set('不会影响抽查')
        self.assertEqual(tuple(self.panel.review_accounts), ACCOUNTS)
        with patch('ledger_viewer.jev_ui.ConsentDialog') as consent:
            self.panel.new_batch()
        batch = consent.call_args.args[1]
        self.assertEqual(batch_counts(self.app.store, batch)['total'], 2)
        self.assertEqual(json.loads(batch_info(self.app.store, batch)['scope_json'])['review_accounts'], list(ACCOUNTS))
        self.panel.review_accounts['其他应付款'].set(True)
        with patch.object(self.panel, 'prepare') as prepare:
            self.panel.new_batch()
            prepare.assert_called_once_with({'review_accounts': ['其他应付款']})

    def test_prompt_editor_changes_each_question_advice_and_base_with_versions(self):
        batch = prepare_batch(self.app.store, {})
        old_rules = json.loads(batch_info(self.app.store, batch)['body_json'])
        editor = ReviewRulesDialog(self.app)
        self.root.update()
        self.assertEqual(len(editor.paths), 64)
        editor.editor.insert('end', '\n用户自定义基础边界。')
        question = next(label for label, path in editor.paths.items() if path == ('topics', 0, 'question'))
        editor.selection.set(question)
        editor._switch()
        editor.editor.insert('end', ' 用户的OR01补充问题。')
        advice = next(label for label, path in editor.paths.items() if path == ('topics', 0, 'advice'))
        editor.selection.set(advice)
        editor._switch()
        editor.editor.delete('1.0', 'end')
        editor.editor.insert('1.0', '用户固定核查建议。')
        editor.model.set('jev-1.14.0')
        editor._save()
        self.root.update()
        new_id, rules = load_rules(self.app.store)
        self.assertGreater(new_id, batch_info(self.app.store, batch)['rule_id'])
        self.assertTrue(rules['common'].endswith('用户自定义基础边界。'))
        self.assertTrue(rules['topics'][0]['question'].endswith('用户的OR01补充问题。'))
        self.assertEqual(rules['topics'][0]['advice'], '用户固定核查建议。')
        self.assertEqual(rules['model'], 'jev-1.14.0')
        self.assertEqual(json.loads(batch_info(self.app.store, batch)['body_json']), old_rules)
        reopened = ReviewRulesDialog(self.app)
        self.assertIn('用户自定义基础边界。', reopened.editor.get('1.0', 'end'))
        reopened.destroy()

    def test_flash_confirm_results_and_full_voucher_details(self):
        batch = prepare_batch(self.app.store, {'review_accounts': ['其他应付款'], 'bailian_free_confirmed': True})
        dialog = ConsentDialog(self.panel, batch)
        self.root.update()
        self.assertIn('股东借款', dialog.preview.get('1.0', 'end'))
        self.assertIn('history', dialog.preview.get('1.0', 'end'))
        self.assertEqual(dialog.total, 2)
        original = ReviewJob.__init__
        def flash(key, payload):
            return {'model': 'deepseek-flash', 'choices': [{'message': {'content': json.dumps({
                'hits': [{'id': 'OP01', 'evidence': '股东借款'}], 'insufficient': False})}}]}
        def factory(job, path, batch_id, api_key, **options):
            original(job, path, batch_id, api_key, deepseek_chat=flash, **options)
        with patch('ledger_viewer.jev_ui.get_api_key', return_value=''), patch('ledger_viewer.jev_ui.get_bailian_key', return_value=''), patch('ledger_viewer.jev_ui.get_deepseek_key', return_value='fictional-key'), patch.object(ReviewJob, '__init__', factory):
            dialog._confirm()
            self.panel.job.thread.join(5)
        self.panel.refresh()
        self.root.update()
        self.assertEqual(batch_info(self.app.store, batch)['status'], 'completed')
        self.assertEqual(len(self.panel.rows), 1)
        group = next(iter(self.panel.rows.values()))
        self.assertIsNone(group['score'])
        self.assertEqual(group['priority'], '明确命中')
        self.assertFalse(group['partial'])
        detail = ReviewDetail(self.panel, group)
        self.root.update()
        self.assertEqual(len(detail.voucher_view.get_children()), 2)
        content = detail._json_content()
        self.assertIn('DeepSeek 明确命中', content)
        self.assertIn('原文依据：股东借款', content)
        self.assertIn('OP01', content)
        self.assertNotIn('相关度', content)
        with patch.object(self.app.workbench, 'choose_issue') as choose:
            button = next(widget for widget in detail.actions.winfo_children()
                          if isinstance(widget, ttk.Button) and widget.cget('text') == '加入尽调问题…')
            button.invoke()
        choose.assert_called_once_with([group['key']])
        detail.destroy()

    def test_bailian_result_shows_prompt_decisions_without_probability(self):
        batch = prepare_batch(self.app.store, {'review_accounts': ['其他应付款'], 'bailian_free_confirmed': True})
        def post(key, payload, endpoint):
            return {'model': 'decision-model-preview', 'answers': {key: decision_answer(key == 'OP01') for key in payload['questions']},
                'usage': {'input_tokens': 100}}
        job = ReviewJob(self.app.store.path, batch, '', bailian_key='fictional-key', bailian_post=post)
        job.start(self.app.store)
        job.thread.join(5)
        self.panel.batch_id = batch
        self.panel.refresh()
        self.root.update()
        group = next(iter(self.panel.rows.values()))
        self.assertEqual(group['provider'], 'bailian')
        self.assertIsNone(group['score'])
        detail = ReviewDetail(self.panel, group)
        self.assertIn('decision-model-preview', detail._json_content())
        self.assertIn('百炼', detail._json_content())
        self.assertIn('OP01 股东及关联方借款：命中', detail._json_content())
        self.assertNotIn('概率', detail._json_content())
        self.assertNotIn('相关度', detail._json_content())
        detail.destroy()

    def test_ai_settings_save_bailian_key_and_jev_optional_priority(self):
        from ledger_viewer.ui import SettingsDialog
        credentials = {'key': ''}
        def save_key(value):
            credentials['key'] = value
        def delete_key():
            credentials['key'] = ''
        with patch.dict(self.app.controller.preferences, {}, clear=True), \
            patch.object(self.app.controller, '_save_preferences') as save_preferences, \
            patch('ledger_viewer.ui.get_api_key', return_value=''), \
            patch('ledger_viewer.ui.deepseek_get_api_key', return_value=''), \
            patch('ledger_viewer.ui.bailian_get_api_key', side_effect=lambda: credentials['key']), \
            patch('ledger_viewer.ui.bailian_save_api_key', side_effect=save_key), \
            patch('ledger_viewer.ui.bailian_delete_api_key', side_effect=delete_key):
            dialog = SettingsDialog(self.app)
            self.root.update()
            self.assertEqual(dialog.review_preferred.get(), '百炼优先（默认）')
            self.assertEqual(dialog.provider_tabs.index(dialog.provider_tabs.select()), 0)
            self.assertEqual(dialog.bailian_entry.cget('show'), '•')
            self.assertLess(dialog.winfo_reqheight(), self.root.winfo_screenheight())
            dialog.bailian_key.set('fictional-bailian-key')
            self.assertTrue(dialog._save_bailian_key())
            self.assertEqual(credentials['key'], 'fictional-bailian-key')
            dialog._delete_bailian_key()
            self.assertFalse(credentials['key'])
            dialog.bailian_key.set('fictional-bailian-key')
            dialog.review_preferred.set('Jev 优先')
            dialog._finish()
            self.assertEqual(credentials['key'], 'fictional-bailian-key')
            self.assertEqual(self.app.controller.preferences['review_preferred_provider'], 'bailian')
            self.assertIn('/systemone', self.app.controller.preferences['bailian_endpoint'])
            save_preferences.assert_called_once()

    def test_specialist_minimum_layout_keeps_controls_visible(self):
        self.app.select_page('review')
        self.app.geometry('820x600')
        self.root.update()
        self.assertTrue(self.panel.start_button.winfo_ismapped())
        self.assertTrue(self.panel.progress_bar.winfo_ismapped())
        editor = ReviewRulesDialog(self.app)
        editor.geometry('820x720')
        self.root.update()
        self.assertGreater(editor.editor.winfo_height(), 80)
        editor.destroy()
        dialog = ConsentDialog(self.panel, prepare_batch(self.app.store, {}))
        dialog.geometry('670x560')
        self.root.update()
        self.assertTrue(dialog.confirm.winfo_ismapped())
        self.assertGreater(dialog.preview.winfo_height(), 50)
        dialog._cancel()

    def test_ai_settings_have_no_tiers_or_numeric_filters_and_menu_is_renamed(self):
        editor = ReviewRulesDialog(self.app)
        self.root.update()
        self.assertEqual(editor.title(), 'AI审查设置 · 当前账套')
        for name in ('thresholds', 'bailian_thresholds', 'filter_vars', 'basis_vars', 'percentile'):
            self.assertFalse(hasattr(editor, name))
        editor.destroy()
        self.assertFalse(hasattr(self.panel, 'tier'))
        self.assertNotIn('priority', self.panel.view.cget('columns'))
        self.assertNotIn('score', self.panel.view.cget('columns'))
        menu = self.app.nametowidget(self.app.cget('menu'))
        settings = self.app.nametowidget(menu.entrycget(menu.index('end'), 'menu'))
        labels = [settings.entrycget(index, 'label') for index in range(settings.index('end') + 1)
                  if settings.type(index) != 'separator']
        self.assertIn('AI审查设置', labels)
        self.assertNotIn('Jev 审查…', labels)


if __name__ == '__main__':
    unittest.main()
