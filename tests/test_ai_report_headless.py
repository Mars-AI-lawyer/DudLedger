"""Headless regressions for the AI report's historical and recovery paths."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.jev_review import batch_info, prepare_batch, voucher_results
from ledger_viewer.jev_ui import (ReviewDetail, ReviewPanel, detail_lines,
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
        self.assertIn('全部 2 个已设置科目', panel.scope_summary.get())
        self.assertIn('未选择 = 全部', panel.scope_summary.get())
        self.assertIn('不继承当前明细筛选', panel.scope_summary.get())
        panel.review_accounts['其他应付款'].set(True)
        panel._update_scope_summary()
        self.assertIn('已选 1 个专项科目', panel.scope_summary.get())

        panel.lane = Variable('uncertain')
        panel.account = Variable('1002')
        panel.period = Variable('2026/1')
        panel.category = Variable('借款')
        panel.show_ignored = Variable(True)
        calls = []
        panel.refresh_rows = lambda: calls.append(True)
        panel._show_all_results()
        self.assertEqual(panel.lane.get(), 'all')
        self.assertEqual(panel.account.get(), '')
        self.assertEqual(panel.period.get(), '全部期间')
        self.assertEqual(panel.category.get(), '全部')
        self.assertFalse(panel.show_ignored.get())
        self.assertEqual(calls, [True])


if __name__ == '__main__':
    unittest.main()
