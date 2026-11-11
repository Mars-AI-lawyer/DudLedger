"""Bailian decision API contracts, secure settings and AI-only fallback coverage."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ledger_viewer import bailian_review
from ledger_viewer.jev_review import ReviewJob, batch_info, batch_counts, prepare_batch, questions_for, voucher_results
from ledger_viewer.specialist_rules import default_rules, load_rules, save_rules
import test_specialist_review as existing


class BailianReviewTest(unittest.TestCase):
    setUp = existing.SpecialistReviewTest.setUp
    tearDown = existing.SpecialistReviewTest.tearDown
    flash = existing.SpecialistReviewTest.flash

    def post(self, key, payload, endpoint):
        self.bailian_calls.append(payload)
        if getattr(self, 'bailian_error', None):
            raise self.bailian_error
        return {'model': 'decision-model-preview', 'answers': {
            key: {'type':'noul', 'noul': .9 if key in ('OP01', 'OR01', 'PIC01') else .01}
            for key in payload['questions']}, 'usage': {'input_tokens': 100}, 'latency_ms': 52.9}

    def run_case(self, scope=None, jev='jev-key', bailian='bailian-key', flash='flash-key'):
        self.bailian_calls = []
        batch = prepare_batch(self.store, scope or {'review_accounts':['其他应付款']})
        job = ReviewJob(self.store.path, batch, jev, existing.JevClient,
            bailian_key=bailian, bailian_post=self.post, deepseek_key=flash, deepseek_chat=self.flash)
        job.start(self.store)
        job.thread.join(10)
        self.assertFalse(job.thread.is_alive())
        self.assertEqual(batch_info(self.store, batch)['status'], 'completed', list(job.events.queue))
        return batch

    def test_bailian_is_default_even_with_jev_configured(self):
        batch = self.run_case()
        self.assertEqual(len(self.bailian_calls), 1)
        self.assertEqual(existing.JevClient.calls, [])
        self.assertFalse(self.flash_calls)
        data = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertEqual(data['provider'], 'bailian')
        self.assertEqual(data['model'], 'decision-model-preview')
        self.assertEqual(data['usage']['output_tokens'], 0)
        group = voucher_results(self.store, batch)[0]
        self.assertIn('bailian', group['categories'][0][2])

    def test_jev_can_be_selected_as_first_choice(self):
        self.run_case({'review_accounts':['其他应付款'], 'preferred_provider':'jev'})
        self.assertEqual(len(existing.JevClient.calls), 1)
        self.assertFalse(self.bailian_calls)

    def test_missing_bailian_falls_back_to_jev_then_missing_both_to_flash(self):
        self.run_case(bailian='')
        self.assertEqual(len(existing.JevClient.calls), 1)
        self.assertFalse(self.bailian_calls)
        batch = self.run_case({'review_accounts':['实收资本']}, jev='', bailian='')
        self.assertEqual(voucher_results(self.store, batch)[0]['provider'], 'deepseek')
        self.assertEqual(len(self.flash_calls), 1)

    def test_bailian_arrearage_uses_jev_and_both_exhausted_use_flash(self):
        self.bailian_error = bailian_review.BailianAPIError(403, 'Arrearage')
        batch = self.run_case()
        self.assertEqual(voucher_results(self.store, batch)[0]['provider'], 'jev')
        class JevQuota(Exception):
            status_code = 402
        existing.JevClient.error = JevQuota('private key')
        batch = self.run_case({'review_accounts':['实收资本']})
        self.assertEqual(voucher_results(self.store, batch)[0]['provider'], 'deepseek')
        data = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertIn('百炼', data['fallback_reason'])
        self.assertIn('Jev', data['fallback_reason'])
        self.assertNotIn('private', json.dumps(data))

    def test_bailian_unavailable_endpoint_falls_back_with_actionable_reason(self):
        self.bailian_error = bailian_review.BailianAPIError(404)
        batch = self.run_case()
        self.assertEqual(voucher_results(self.store, batch)[0]['provider'], 'jev')
        data = json.loads(self.store.db.execute('SELECT response_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertIn('业务空间', data['fallback_reason'])

    def test_each_model_uses_its_own_thresholds(self):
        _, rules = load_rules(self.store)
        rules['thresholds'] = {'谨慎':95, '中':97, '宽松':99}
        rules['bailian_thresholds'] = {'谨慎':50, '中':65, '宽松':85}
        save_rules(self.store, rules)
        batch = self.run_case()
        self.assertEqual(len(voucher_results(self.store, batch)), 1)
        _, rules = load_rules(self.store)
        rules['bailian_thresholds'] = {'谨慎':95, '中':97, '宽松':99}
        save_rules(self.store, rules)
        self.assertEqual(voucher_results(self.store, batch), [])

    def test_switching_preferred_model_does_not_reuse_other_model_cache(self):
        self.run_case()
        batch = self.run_case({'review_accounts':['其他应付款'], 'preferred_provider':'jev'})
        self.assertEqual(batch_counts(self.store, batch)['cached'], 0)
        self.assertEqual(voucher_results(self.store, batch)[0]['provider'], 'jev')

    def test_older_specialist_rules_gain_bailian_settings_without_overwrite(self):
        rules = default_rules()
        rules.pop('bailian_thresholds')
        rules['common'] += ' 用户原有提示词。'
        with self.store.db:
            old = self.store.db.execute('INSERT INTO jev_rules(body_json) VALUES(?)', (json.dumps(rules),)).lastrowid
        new, updated = load_rules(self.store)
        self.assertGreater(new, old)
        self.assertEqual(updated['common'], rules['common'])
        self.assertIn('bailian_thresholds', updated)
        self.assertNotIn('bailian_thresholds', json.loads(self.store.db.execute('SELECT body_json FROM jev_rules WHERE id=?', (old,)).fetchone()[0]))


class BailianTransportTest(unittest.TestCase):
    def test_31_native_noul_questions_split_at_16_and_usage_aggregates(self):
        calls = []
        def post(key, payload, endpoint):
            calls.append(payload)
            return {'model':'decision-model-preview', 'answers':{key:{'type':'noul','noul':.8}
                for key in payload['questions']}, 'usage':{'input_tokens':11}}
        questions = questions_for(default_rules())
        with bailian_review.DecisionClient(api_key='fake-key', post_fn=post) as client:
            raw = client.system_one(state={'summary':'虚构样例'}, questions=questions).model_dump()
        self.assertEqual([len(call['questions']) for call in calls], [16, 15])
        self.assertEqual(len(raw['answers']), 31)
        self.assertEqual(raw['usage'], {'input_tokens':22, 'output_tokens':0})
        for call in calls:
            self.assertEqual(set(call), {'model','state','questions'})
            self.assertNotIn('messages', call)
            self.assertNotIn('response_format', call)
            self.assertIn('criteria', next(iter(call['questions'].values())))

    def test_pipeline_sends_all_thirty_questions_within_budget(self):
        from ledger_viewer.jev_review import request_review
        calls = []
        def post(key, payload, endpoint):
            calls.append(payload)
            return {'model': 'decision-model-preview', 'answers': {key: {'type': 'noul', 'noul': .01}
                for key in payload['questions']}, 'usage': {'input_tokens': 10}}
        rules = default_rules()
        rules['model'] = 'decision-model-preview'
        result = request_review([{'name': '实收资本', 'summary': '虚构样例', 'debit': '10.00'}],
            rules, 'test-key', threading.Event(), lambda **options: bailian_review.DecisionClient(**options, post_fn=post))
        self.assertEqual(result['status'], 'success')
        self.assertEqual([len(call['questions']) for call in calls], [11, 11, 11])
        self.assertEqual(len(result['items'][0]['answers']), 31)
        self.assertEqual(result['items'][0]['question_batches'], 3)
        self.assertFalse(result['items'][0]['needs_manual_review'])

    def test_split_request_rechecks_cancellation_and_snapshot_each_time(self):
        cancelled = threading.Event()
        def post(key, payload, endpoint):
            cancelled.set()
            return {'model':'decision-model-preview', 'answers':{key:{'type':'noul','noul':.5}
                for key in payload['questions']}, 'usage':{'input_tokens':1}}
        with bailian_review.DecisionClient(api_key='fake', post_fn=post, cancelled=cancelled.is_set) as client:
            with self.assertRaises(bailian_review.ReviewCancelled):
                client.system_one(state={}, questions=questions_for(default_rules()))
        with bailian_review.DecisionClient(api_key='fake', before_send=lambda: False) as client:
            with self.assertRaises(bailian_review.ReviewSnapshotChanged):
                client.system_one(state={}, questions=questions_for(default_rules()))

    def test_endpoint_and_auth_modes_follow_official_gateways(self):
        from io import BytesIO
        endpoints = ['https://dashscope.aliyuncs.com/compatible-mode/v1/systemone', bailian_review.DEFAULT_ENDPOINT,
            'https://123456.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone',
            'https://123456.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone']
        seen = []
        def open_request(request, **options):
            seen.append(request.get_header('Authorization'))
            return BytesIO(b'{}')
        with patch.object(bailian_review.urllib.request, 'urlopen', open_request):
            for endpoint in endpoints:
                bailian_review.post('fake-key', {}, endpoint)
        self.assertEqual(seen, ['fake-key', 'Bearer fake-key', 'Bearer fake-key', 'Bearer fake-key'])
        for invalid in ['http://dashscope.aliyuncs.com/compatible-mode/v1/systemone',
            'https://example.com/compatible-mode/v1/systemone',
            'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
            'https://evil.maas.aliyuncs.com/compatible-mode/v1/systemone']:
            with self.assertRaises(ValueError):
                bailian_review.validate_endpoint(invalid)

    def test_missing_questions_and_wrong_model_are_rejected(self):
        for result in [{'model':'other-model','answers':{},'usage':{'input_tokens':1}},
            {'model':'decision-model-preview','answers':{},'usage':{'input_tokens':1}}]:
            with bailian_review.DecisionClient(api_key='fake', post_fn=lambda *args: result) as client:
                with self.assertRaises(ValueError):
                    client.system_one(state={}, questions=questions_for(default_rules()))

    def test_connection_probe_only_sends_fixed_fictional_content(self):
        calls = []
        def post(key, payload, endpoint):
            calls.append(payload)
            return {'model': 'decision-model-preview', 'answers': {key: {'type': 'noul', 'noul': .01}
                for key in payload['questions']}, 'usage': {'input_tokens': 10}}
        result = bailian_review.test_connection('test-key', post_fn=post)
        self.assertEqual(result['status'], 'success')
        self.assertIn('虚构样例', str(calls))
        self.assertEqual(len(calls[0]['questions']), 6)
        self.assertNotIn('test-key', json.dumps(calls))

    def test_insecure_keyring_uses_memory_and_does_not_write_plaintext(self):
        with patch.object(bailian_review, '_SESSION_KEY', None), patch.object(bailian_review, '_MEMORY_ONLY', False), patch.object(bailian_review, 'keyring') as keys:
            keys.get_keyring.return_value = SimpleNamespace()
            bailian_review.save_api_key('temporary-test-key')
            self.assertEqual(bailian_review.get_api_key(), 'temporary-test-key')
            self.assertTrue(bailian_review.key_memory_only())
            keys.set_password.assert_not_called()
            bailian_review.delete_api_key()
            self.assertEqual(bailian_review.get_api_key(), '')


if __name__ == '__main__':
    unittest.main()
