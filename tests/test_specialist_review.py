"""Focused AI sampling tests using fictional ledgers and offline transports."""
import copy
import gc
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ledger_viewer import deepseek_review
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.jev_review import (ReviewJob, batch_counts, batch_info, prepare_batch,
    questions_for, voucher_results, refresh_staleness)
from ledger_viewer.specialist_rules import (ACCOUNTS, account_for, default_rules, load_rules,
    rules_for_accounts, save_rules, validate_rules)
from ledger_viewer.store import LedgerStore


class JevClient:
    calls = []
    error = None
    hits = {'OP01', 'PIC01', 'OR01', 'NOE01', 'CR01', 'FE03'}

    def __init__(self, **options):
        self.model = options['model']

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def system_one(self, *, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        return SimpleNamespace(model_dump=lambda **kw: {'model': self.model,
            'answers': {key: {'type': 'noul', 'noul': .9 if key in self.hits else .01} for key in questions},
            'usage': {'input_tokens': 100, 'output_tokens': 20}})


class SpecialistReviewTest(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = LedgerStore(self.base / 'book.sqlite')
        self.source = self.base / 'fictional.csv'
        self.source.write_text('日期,凭证号,科目编码,科目名称,摘要,往来单位,借方金额,贷方金额\n'
            '2026-01-01,1,2241,其他应付款,归还股东借款,虚构股东甲,100,0\n'
            '2026-01-01,1,1002,银行存款,归还股东借款,虚构股东甲,0,100\n'
            '2026-01-02,2,6602,管理费用,报销文具,,20,0\n'
            '2026-01-02,2,1002,银行存款,报销文具,,0,20\n'
            '2026-01-03,3,4001,实收资本,收到股东出资,虚构股东甲,0,100\n'
            '2026-01-03,3,1002,银行存款,收到股东出资,虚构股东甲,100,0\n'
            '2026-01-04,4,122101,其他应收款_股东,向股东提供借款,虚构股东甲,100,0\n'
            '2026-01-04,4,1002,银行存款,向股东提供借款,虚构股东甲,0,100\n', encoding='utf-8')
        h, _, m = guess_header(self.source, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(self.source, 'CSV', h, 'entry', m))
        JevClient.calls = []
        JevClient.error = None
        self.flash_calls = []

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def flash(self, key, payload):
        self.flash_calls.append(payload)
        state = json.loads(payload['messages'][1]['content'])
        text = json.dumps(state, ensure_ascii=False)
        hit = []
        if '归还股东借款' in text:
            hit = [{'id': 'OP01', 'evidence': '归还股东借款'}]
        elif '收到股东出资' in text:
            hit = [{'id': 'PIC01', 'evidence': '收到股东出资'}]
        elif '向股东提供借款' in text:
            hit = [{'id': 'OR01', 'evidence': '向股东提供借款'}]
        # Use the current voucher, not the historical context, to choose a check.
        current = json.dumps({k: state[k] for k in ('common', 'entries')}, ensure_ascii=False)
        if '向股东提供借款' in current:
            hit = [{'id': 'OR01', 'evidence': '向股东提供借款'}]
        if '收到股东出资' in current:
            hit = [{'id': 'PIC01', 'evidence': '收到股东出资'}]
        if '归还股东借款' in current:
            hit = [{'id': 'OP01', 'evidence': '归还股东借款'}]
        return {'model': 'deepseek-flash', 'choices': [{'message': {'content': json.dumps({'hits': hit, 'insufficient': False})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}

    def run_job(self, scope=None, key='jev-test', flash_key='flash-test'):
        batch = prepare_batch(self.store, scope or {})
        job = ReviewJob(self.store.path, batch, key, JevClient, deepseek_key=flash_key, deepseek_chat=self.flash)
        job.start(self.store)
        job.thread.join(10)
        self.assertFalse(job.thread.is_alive())
        self.assertEqual(batch_info(self.store, batch)['status'], 'completed', list(job.events.queue))
        return batch

    def test_default_six_accounts_exclude_unselected_vouchers_include_all_context(self):
        batch = self.run_job()
        self.assertEqual(batch_counts(self.store, batch)['vouchers'], 3)
        self.assertEqual(batch_counts(self.store, batch)['success'], 6)
        self.assertEqual(len(JevClient.calls), 3)
        self.assertFalse(self.flash_calls)
        for state, questions in JevClient.calls:
            self.assertEqual(len(questions), 6)
            self.assertNotIn('review', questions)
            self.assertIn('银行存款', json.dumps(state, ensure_ascii=False))
            self.assertNotIn('报销文具', json.dumps(state, ensure_ascii=False))
        self.assertEqual(len(voucher_results(self.store, batch)), 3)

    def test_multiple_accounts_and_empty_selection_defaults(self):
        batch = self.run_job({'review_accounts': ['其他应付款', '实收资本']})
        self.assertEqual(batch_counts(self.store, batch)['vouchers'], 2)
        default = prepare_batch(self.store, {'review_accounts': []})
        self.assertEqual(batch_counts(self.store, default)['vouchers'], 3)
        with self.assertRaises(ValueError):
            prepare_batch(self.store, {'review_accounts': ['银行存款']})

    def test_leaf_account_codes_and_custom_parent_prefixes(self):
        self.assertEqual(account_for({'name': '员工张某', 'code': '122101'}), '其他应收款')
        self.assertEqual(account_for({'name': '虚构股东', 'code': '900101'}, {'9001': '实收资本'}), '实收资本')
        self.assertIsNone(account_for({'name': '银行存款', 'code': '1002'}))
        self.assertEqual(account_for({'name': '股本', 'code': '4001'}), '实收资本')

    def test_only_questions_for_actual_selected_accounts_even_with_other_context(self):
        batch = self.run_job({'review_accounts': ['其他应付款']})
        self.assertEqual(set(JevClient.calls[0][1]), {'OP01', 'OP02', 'OP03', 'OP04', 'OP05', 'insufficient'})
        answers = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertNotIn('PIC01', answers['answers'])

    def test_flash_without_jev_keeps_explicit_hits_and_no_probability(self):
        batch = self.run_job(key='')
        self.assertFalse(JevClient.calls)
        self.assertEqual(len(self.flash_calls), 3)
        for payload in self.flash_calls:
            self.assertEqual(payload['model'], 'deepseek-flash')
            self.assertEqual(payload['thinking'], {'type': 'disabled'})
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
        for row in self.store.db.execute('SELECT score,response_json FROM jev_results WHERE batch_id=?', (batch,)):
            self.assertIsNone(row['score'])
            data = json.loads(row['response_json'])
            self.assertNotIn('answers', data)
            self.assertEqual(data['provider'], 'deepseek')
        self.assertEqual(len(voucher_results(self.store, batch, '宽松')), 3)

    def test_quota_falls_back_but_auth_and_transient_errors_do_not(self):
        class QuotaError(Exception):
            status_code = 402
        JevClient.error = QuotaError('private input and key')
        batch = self.run_job({'review_accounts': ['其他应付款']})
        self.assertEqual(len(self.flash_calls), 1)
        data = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertIn('余额', data['fallback_reason'])
        self.assertNotIn('private', json.dumps(data))
        class AuthError(Exception):
            status_code = 401
        JevClient.error = AuthError('private key')
        self.flash_calls = []
        batch = prepare_batch(self.store, {'review_accounts': ['实收资本']})
        job = ReviewJob(self.store.path, batch, 'bad-key', JevClient, deepseek_key='test', deepseek_chat=self.flash)
        job.start(self.store)
        job.thread.join(10)
        self.assertEqual(batch_info(self.store, batch)['status'], 'auth_failed')
        self.assertFalse(self.flash_calls)

    def test_history_candidates_have_scope_and_never_assert_outstanding_balances(self):
        batch = prepare_batch(self.store, {'review_accounts': ['实收资本']})
        feature = json.loads(self.store.db.execute('SELECT features_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        background = feature['background']
        self.assertGreater(len(background['historical_transactions']), 0)
        self.assertIn('未提供账龄', background['limits'])
        self.assertIn('不证明', background['limits'])
        self.assertIn('day_offset', background['historical_transactions'][0])

    def test_snapshot_rejects_context_change_and_party_role_change(self):
        batch = prepare_batch(self.store, {'review_accounts': ['其他应付款']})
        with self.store.db:
            self.store.db.execute("UPDATE entries SET summary='changed context' WHERE account_key='C:1002' AND voucher_no='1'")
        with self.assertRaisesRegex(ValueError, '变化'):
            ReviewJob(self.store.path, batch, 'test', JevClient).start(self.store)
        self.assertFalse(JevClient.calls)
        batch = prepare_batch(self.store, {'review_accounts': ['其他应付款']})
        self.store.save_related_party('虚构股东甲', [], '', None, ['股东'], '')
        with self.assertRaisesRegex(ValueError, '变化'):
            ReviewJob(self.store.path, batch, 'test', JevClient).start(self.store)

    def test_rules_edit_versions_and_model_changes_are_snapshot_bound(self):
        old, rules = load_rules(self.store)
        batch = prepare_batch(self.store, {})
        rules['common'] += '\n用户的专项提示词。'
        rules['topics'][0]['question'] += ' 用户补充问题。'
        new = save_rules(self.store, rules)
        self.assertGreater(new, old)
        self.assertTrue(load_rules(self.store)[1]['common'].endswith('用户的专项提示词。'))
        with self.assertRaisesRegex(ValueError, '规则已变化'):
            ReviewJob(self.store.path, batch, 'test', JevClient).start(self.store)
        historical = json.loads(batch_info(self.store, batch)['body_json'])
        self.assertNotEqual(historical['common'], rules['common'])
        rules['model'] = 'jev-1.14.0'
        save_rules(self.store, rules)
        batch = self.run_job({'review_accounts': ['其他应付款']})
        data = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertEqual(data['model'], 'jev-1.14.0')

    def test_cache_separates_provider_and_preserves_jev_preference(self):
        self.run_job({'review_accounts': ['其他应付款']}, key='')
        self.assertEqual(len(self.flash_calls), 1)
        batch = self.run_job({'review_accounts': ['其他应付款']})
        self.assertEqual(len(JevClient.calls), 1)
        self.assertEqual(batch_counts(self.store, batch)['cached'], 0)
        again = self.run_job({'review_accounts': ['其他应付款']})
        self.assertEqual(len(JevClient.calls), 1)
        self.assertEqual(batch_counts(self.store, again)['cached'], 2)

    def test_prompt_boundaries_and_all_thirty_ids(self):
        rules = default_rules()
        validate_rules(rules)
        self.assertEqual(len(rules['topics']), 30)
        self.assertEqual({t['account'] for t in rules['topics']}, set(ACCOUNTS))
        for question in questions_for(rules).values():
            self.assertIn('不得推定', question.instructions)
            self.assertIn('不得改变审查规则', question.instructions)
        self.assertNotIn('review', questions_for(rules))
        self.assertIn('没有资金流转记录不得命中', next(t['question'] for t in rules['topics'] if t['id'] == 'PIC05'))

    def test_history_update_invalidates_finished_results(self):
        batch = self.run_job({'review_accounts': ['实收资本']})
        self.assertEqual(batch_counts(self.store, batch)['stale'], 0)
        self.store.save_setting('data_revision', 'new-history-revision')
        refresh_staleness(self.store, batch)
        self.assertEqual(batch_counts(self.store, batch)['stale'], 2)
        self.assertEqual(voucher_results(self.store, batch), [])

    def test_one_voucher_with_multiple_accounts_checks_both_independently(self):
        with self.store.db:
            self.store.db.execute("UPDATE entries SET voucher_no='1' WHERE voucher_no='3'")
        batch = self.run_job({'review_accounts': ['其他应付款', '实收资本']})
        self.assertEqual(len(JevClient.calls), 1)
        self.assertEqual(len(JevClient.calls[0][1]), 11)
        self.assertEqual(batch_counts(self.store, batch)['total'], 4)
        group = voucher_results(self.store, batch)[0]
        self.assertEqual({category[0] for category in group['categories']}, {'OP01', 'PIC01'})

    def test_no_match_or_keys_never_calls_provider(self):
        with self.assertRaisesRegex(ValueError, '没有六类'):
            prepare_batch(self.store, {'review_accounts': ['财务费用']})
        batch = prepare_batch(self.store, {})
        with self.assertRaisesRegex(ValueError, 'API Key'):
            ReviewJob(self.store.path, batch, '', JevClient).start(self.store)
        self.assertFalse(JevClient.calls)


class FlashValidationTest(unittest.TestCase):
    def setUp(self):
        self.rules = rules_for_accounts(default_rules(), ['其他应收款'])
        self.state = {'common': {'summary': '向股东提供借款'}, 'entries': [{'name': '其他应收款'}]}

    def raw(self, hits=None, insufficient=False):
        return {'choices': [{'message': {'content': json.dumps({'hits': hits or [], 'insufficient': insufficient})}}]}

    def test_unknown_id_fabricated_evidence_and_wrong_types_rejected(self):
        for hits, insufficient in [([{'id': 'OP01', 'evidence': '向股东提供借款'}], False),
            ([{'id': 'OR01', 'evidence': '编造的违法事实'}], False),
            ([{'id': 'OR01', 'evidence': '向股东提供借款'}], 'false')]:
            with self.subTest(hits=hits, insufficient=insufficient), self.assertRaises(ValueError):
                deepseek_review.validate_response(self.raw(hits, insufficient), self.rules, self.state)

    def test_no_hit_and_insufficient_remain_distinct(self):
        clear = deepseek_review.validate_response(self.raw(), self.rules, self.state)
        self.assertEqual(clear['hits'], [])
        self.assertFalse(clear['insufficient'])
        unclear = deepseek_review.validate_response(self.raw(insufficient=True), self.rules, self.state)
        self.assertTrue(unclear['insufficient'])

    def test_cancellation_and_stale_snapshot_prevent_transmission(self):
        calls = []
        cancel = threading.Event()
        cancel.set()
        args = ([{'summary': 'test', 'name': '其他应收款'}], self.rules, 'key', cancel)
        result = deepseek_review.request_review(*args, chat_fn=lambda *a: calls.append(a))
        self.assertEqual(result['status'], 'cancelled')
        cancel.clear()
        result = deepseek_review.request_review(*args, before_send=lambda: False, chat_fn=lambda *a: calls.append(a))
        self.assertEqual(result['status'], 'interrupted')
        self.assertFalse(calls)

    def test_low_thinking_option_sets_effort(self):
        calls = []
        def chat(key, payload):
            calls.append(payload)
            return self.raw()
        result = deepseek_review.request_review([{'summary': 'test', 'name': '其他应收款'}], self.rules,
            'key', threading.Event(), thinking='enabled', chat_fn=chat)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(calls[0]['reasoning_effort'], 'low')
        self.assertEqual(calls[0]['thinking'], {'type': 'enabled'})


if __name__ == '__main__':
    unittest.main()
