"""Headless regressions for the AI report's historical and recovery paths."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer.issue_store import mark_vouchers
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.jev_review import batch_info, prepare_batch, voucher_results
from ledger_viewer.jev_ui import (ReviewDetail, ReviewPanel, account_choice_positions, detail_lines, review_toolbar,
    saved_voucher_lines, sent_snapshot_content)
from ledger_viewer.store import LedgerStore


class Variable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class AIReportHeadlessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.store = LedgerStore(base / 'book.sqlite')
        source = base / 'fictional.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
            '2026-01-01,1,2241,其他应付款,股东借款,0,100\n'
            '2026-01-01,1,1002,银行存款,股东借款,100,0\n', encoding='utf-8')
        header, _, mapping = guess_header(source, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(source, 'CSV', header, 'entry', mapping))
        self.batch = prepare_batch(self.store, {})
        self.group = voucher_results(self.store, self.batch, lane='all')[0]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_relationship_edit_refreshes_staleness_without_ledger_revision_change(self):
        party_id = self.store.save_related_party('股东甲', [], '', None, [], '持股30%')
        batch = prepare_batch(self.store, {})
        with self.store.db:
            self.store.db.execute("UPDATE jev_batches SET status='completed' WHERE id=?", (batch,))
        panel = ReviewPanel.__new__(ReviewPanel)
        panel.app = SimpleNamespace(store=self.store, import_busy=False)
        panel.preparing = False
        panel.refresh_accounts = Mock()
        panel.batch_id = batch
        panel._stale_token = None
        panel.batch_combo = Mock()
        panel.batch = Variable()
        panel.overview = Variable()
        panel.refresh_rows = Mock()
        panel.refresh()
        self.assertEqual(self.store.db.execute('SELECT MAX(stale) FROM jev_results WHERE batch_id=?', (batch,)).fetchone()[0], 0)
        before = self.store.setting('data_revision', '0')
        self.store.save_related_party('股东甲', [], '', party_id, [], '已退出股东')
        self.assertEqual(self.store.setting('data_revision', '0'), before)
        panel.refresh()
        self.assertEqual(self.store.db.execute('SELECT MIN(stale) FROM jev_results WHERE batch_id=?', (batch,)).fetchone()[0], 1)

    def test_deleted_voucher_uses_saved_rows_send_content_model_and_rules(self):
        expected = saved_voucher_lines(self.group)
        self.assertEqual([row['summary'] for row in expected], ['股东借款', '股东借款'])
        old_info = dict(batch_info(self.store, self.batch))
        self.store.delete_source(1)
        self.assertEqual(self.store.voucher_lines(self.group['lines'][0]['entry_id']), [])
        historical_group = voucher_results(self.store, self.batch, lane='unfinished')[0]
        lines, historical = detail_lines(historical_group, [])
        self.assertTrue(historical)
        self.assertEqual([dict(row) for row in lines], [dict(row) for row in expected])
        sent = sent_snapshot_content(historical_group)
        self.assertIn('摘要：股东借款', sent)
        self.assertIn('贷方金额：100.00', sent)
        self.assertIn('借方金额：100.00', sent)
        self.assertEqual(dict(batch_info(self.store, self.batch)), old_info)

        response = {'model': 'offline-historical-model', 'provider': 'bailian', 'usage': {},
            'answers': {'OP01': {'type': 'choice', 'choice': 'hit'}}}
        historical_group['lines'][0].update(response_json=json.dumps(response), cached=0,
            confidence=1, insufficient=0)
        detail = ReviewDetail.__new__(ReviewDetail)
        detail.group = historical_group
        detail.rules = json.loads(old_info['body_json'])
        content = detail._json_content()
        self.assertIn('offline-historical-model', content)
        self.assertIn('OP01', content)
        self.assertIn('完整结构化回答', content)

    def test_stale_voucher_uses_snapshot_even_when_replacement_rows_exist(self):
        current = [dict(row, summary='当前已修改') for row in saved_voucher_lines(self.group)]
        for row in self.group['lines']:
            row['stale'] = 1
        lines, historical = detail_lines(self.group, current)
        self.assertTrue(historical)
        self.assertEqual([row['summary'] for row in lines], ['股东借款', '股东借款'])
        for row in self.group['lines']:
            row['stale'] = 0
        lines, historical = detail_lines(self.group, current)
        self.assertFalse(historical)
        self.assertIs(lines, current)

    def test_read_only_guards_do_not_write_feedback_or_attention(self):
        detail = ReviewDetail.__new__(ReviewDetail)
        detail.historical = True
        detail.feedback = Variable()
        self.assertIsNone(detail._save_ignore())
        self.assertFalse(detail._save_attention())
        self.assertIn('原凭证已变化或不存在', detail.feedback.get())
        destroyed = []
        detail.destroy = lambda: destroyed.append(True)
        self.assertTrue(detail.request_close())
        self.assertEqual(destroyed, [True])

    def test_scope_summary_and_zero_result_action_restore_all(self):
        panel = ReviewPanel.__new__(ReviewPanel)
        panel.review_accounts = {'其他应收款': Variable(False), '其他应付款': Variable(False)}
        panel.scope_summary = Variable()
        panel._update_scope_summary()
        self.assertEqual(panel.scope_summary.get(), '抽查科目 · 未勾选即全部（2 个）')
        panel.review_accounts['其他应付款'].set(True)
        panel._update_scope_summary()
        self.assertEqual(panel.scope_summary.get(), '抽查科目 · 已选 1 个')

        panel.lane = Variable('candidates')
        panel.category = Variable('借款')
        panel.show_ignored = Variable(True)
        calls = []
        panel.refresh_rows = lambda: calls.append(True)
        panel._show_all_results()
        self.assertEqual(panel.lane.get(), 'all')
        self.assertEqual(panel.category.get(), '全部')
        self.assertFalse(panel.show_ignored.get())
        self.assertEqual(calls, [True])

    def test_more_options_does_not_hide_visible_account_choices(self):
        panel = ReviewPanel.__new__(ReviewPanel)
        panel.options, panel.scope_options = Mock(), Mock()
        panel.more_button, panel.progress_label = Mock(), Mock()
        panel.options.winfo_manager.return_value = ''
        panel._toggle_options()
        panel.options.pack.assert_called_once_with(fill='x', padx=6, before=panel.progress_label)
        panel.options.winfo_manager.return_value = 'pack'
        panel._toggle_options()
        panel.options.pack_forget.assert_called_once_with()
        self.assertEqual(panel.scope_options.mock_calls, [])

    def test_narrow_toolbar_wraps_independently_of_the_result_table(self):
        bar, actions, paging = Mock(), Mock(), Mock()
        actions.winfo_reqwidth.return_value = 210
        paging.winfo_reqwidth.return_value = 180
        review_toolbar(bar, actions, paging)
        reflow = bar.bind.call_args.args[1]
        reflow(SimpleNamespace(width=380))
        paging.grid_configure.assert_called_once_with(row=1, column=0, columnspan=2, pady=(3, 0))
        reflow(SimpleNamespace(width=650))
        self.assertEqual(paging.grid_configure.call_args.kwargs['row'], 0)

    def test_account_choices_fill_the_width_instead_of_stopping_at_three_columns(self):
        positions, rows = account_choice_positions(650, [90] * 12)
        self.assertEqual(rows, 2)
        self.assertEqual(sum(row == 0 for _left, row, _width in positions), 6)
        positions, rows = account_choice_positions(260, [250, 72, 73, 60])
        self.assertEqual(rows, 2)
        self.assertEqual([row for _left, row, _width in positions], [0, 1, 1, 1])

    def test_account_area_only_scrolls_when_more_than_three_rows_are_required(self):
        panel = ReviewPanel.__new__(ReviewPanel)
        panel.account_canvas, panel.account_scroll, panel.account_selection = Mock(), Mock(), Mock()
        panel.account_window = 'choices'
        panel.account_scroll.winfo_reqwidth.return_value = 15
        panel.account_selection.winfo_children.return_value = [
            Mock(winfo_reqwidth=Mock(return_value=90), winfo_reqheight=Mock(return_value=24))
            for _index in range(12)]
        panel._layout_account_choices(SimpleNamespace(width=650))
        self.assertEqual(panel.account_canvas.configure.call_args.kwargs['height'], 48)
        panel.account_scroll.grid_remove.assert_called_once_with()
        panel._layout_account_choices(SimpleNamespace(width=300))
        self.assertEqual(panel.account_canvas.configure.call_args.kwargs['height'], 72)
        self.assertEqual(panel.account_canvas.configure.call_args.kwargs['scrollregion'], (0, 0, 285, 144))
        panel.account_scroll.grid.assert_called_once()

    def test_start_button_prepares_and_starts_in_place_without_preview(self):
        panel = ReviewPanel.__new__(ReviewPanel)
        panel.app = SimpleNamespace(store=self.store, import_busy=False, controller=SimpleNamespace(preferences={}))
        panel.preparing = False
        panel.review_accounts = {'其他应付款': Variable(True)}
        panel.progress, panel.lane, panel.category, panel.show_ignored = Variable(), Variable(), Variable(), Variable()
        panel.start_button, panel.cancel_button, panel.refresh, panel.view = Mock(), Mock(), Mock(), Mock()
        panel.view.selection.return_value = ()
        with patch('ledger_viewer.jev_ui.ConsentDialog') as preview, patch('ledger_viewer.jev_ui.ReviewJob') as job, \
             patch('ledger_viewer.jev_ui.get_api_key', return_value='fake'), \
             patch('ledger_viewer.jev_ui.get_deepseek_key', return_value=''), patch('ledger_viewer.jev_ui.get_bailian_key', return_value=''):
            panel.new_batch()
            preview.assert_not_called()
            job.return_value.start.assert_called_once_with(self.store)
            job.return_value.start.side_effect = ValueError('请检查密钥')
            panel.new_batch()
            self.assertIn('请检查密钥', panel.progress.get())
        self.assertEqual(panel.lane.get(), 'candidates')
        self.assertEqual(json.loads(batch_info(self.store, panel.batch_id)['scope_json'])['review_accounts'], ['其他应付款'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jev_batches').fetchone()[0], 2)

    def test_bulk_actions_only_use_current_vouchers_without_opening_details(self):
        source = self.store.path.parent / 'second.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                          '2026-01-02,2,2241,其他应付款,股东借款,0,50\n', encoding='utf-8')
        header, _, mapping = guess_header(source, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(source, 'CSV', header, 'entry', mapping))
        batch = prepare_batch(self.store, {})
        groups = voucher_results(self.store, batch, lane='all')
        self.assertEqual(len(groups), 2)
        keys = tuple(group['key'] for group in groups)
        panel = ReviewPanel.__new__(ReviewPanel)
        panel._result_scope = (self.store.path, batch)
        panel.rows = {group['key']: group for group in groups}
        panel.rows.update(history={'local_total': 1, 'lines': [{'stale': 1}]},
                          missing={'local_total': 0, 'lines': [{'stale': 0}]})
        panel.view = Mock()
        panel.view.get_children.return_value = tuple(panel.rows)
        panel.view.selection.return_value = (*keys, 'history', 'missing')
        panel.mark_button, panel.join_button, panel.select_page_button = Mock(), Mock(), Mock()
        panel.selection_label = Variable()
        panel.app = SimpleNamespace(workbench=Mock(), mark_voucher_keys=Mock(side_effect=lambda selected: mark_vouchers(self.store, selected)))
        panel.open_detail = Mock()
        mark_vouchers(self.store, keys[:1])
        self.store.db.execute("UPDATE voucher_marks SET note='已有备注' WHERE voucher_key=?", keys[:1])
        self.store.db.commit()
        self.assertEqual(panel._select_page(), 'break')
        panel.view.selection_set.assert_called_once_with(list(keys))
        self.assertIn('跳过 2 张', panel.selection_label.get())
        panel._mark_selected()
        panel._join_selected()
        panel.app.mark_voucher_keys.assert_called_once_with(keys)
        panel.app.workbench.choose_issue.assert_called_once_with(keys)
        panel.open_detail.assert_not_called()
        self.assertEqual(dict(self.store.db.execute('SELECT voucher_key,note FROM voucher_marks').fetchall()),
                         {keys[0]: '已有备注', keys[1]: ''})
        panel._result_scope = None
        panel._mark_selected()
        panel._join_selected()
        panel.app.mark_voucher_keys.assert_called_once()
        panel.app.workbench.choose_issue.assert_called_once()


if __name__ == '__main__':
    unittest.main()
