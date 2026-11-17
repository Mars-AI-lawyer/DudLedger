"""Prompt verdicts must never be overridden by numerical screening settings."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_specialist_review as fixtures
from ledger_viewer.dd_features import load_config, save_config
from ledger_viewer.jev_review import (batch_info, prepare_batch, request_review,
                                      questions_for, validate_response, voucher_results)
from ledger_viewer.specialist_rules import default_rules, load_rules, rules_for_accounts, save_rules
from ledger_viewer import deepseek_review


class PromptOnlyReviewTest(unittest.TestCase):
    setUp = fixtures.SpecialistReviewTest.setUp
    tearDown = fixtures.SpecialistReviewTest.tearDown
    run_job = fixtures.SpecialistReviewTest.run_job
    flash = fixtures.SpecialistReviewTest.flash

    def test_old_model_and_provider_conditions_migrate_once_without_changing_history(self):
        stored = default_rules()
        stored['model'] = 'jev-1.13.0'
        topic = stored['topics'][0]
        topic['question'] = '用户的自定义问题。'
        topic['bailian_criteria'] = {'true': '用户的命中条件。', 'false': '用户的排除条件。'}
        stored['topics'][1]['criteria_mode'] = 'prompt'
        with self.store.db:
            old = self.store.db.execute('INSERT INTO jev_rules(body_json) VALUES(?)',
                                        (json.dumps(stored),)).lastrowid
        version, rules = load_rules(self.store)
        self.assertGreater(version, old)
        self.assertEqual(rules['model'], 'jev-1.14.0')
        self.assertEqual(rules['topics'][0]['question'],
                         '用户的自定义问题。\n补充命中条件：用户的命中条件。\n排除条件：用户的排除条件。')
        self.assertTrue(all('bailian_criteria' not in t and 'criteria_mode' not in t for t in rules['topics']))
        self.assertEqual(load_rules(self.store), (version, rules))
        saved = save_rules(self.store, rules)
        self.assertEqual(load_rules(self.store), (saved, rules))
        historical = self.store.db.execute('SELECT body_json FROM jev_rules WHERE id=?', (old,)).fetchone()[0]
        self.assertEqual(json.loads(historical), stored)

    def test_jev_and_bailian_receive_identical_questions_and_conditions(self):
        rules = default_rules()
        rules['topics'][0]['bailian_criteria'] = {'true': '仅在明确记载代偿时命中。', 'false': '普通付款排除。'}
        jev = questions_for(rules)
        bailian_rules = copy.deepcopy(rules)
        bailian_rules['model'] = 'decision-model-preview'
        bailian = questions_for(bailian_rules)
        self.assertEqual({k: v.model_dump() for k, v in jev.items()},
                         {k: v.model_dump() for k, v in bailian.items()})
        self.assertIn('普通付款排除。', jev[rules['topics'][0]['id']].instructions)

    def test_deepseek_receives_shared_base_topic_and_insufficient_prompts(self):
        rules = default_rules()
        rules['common'] = '用户的通用审查边界。'
        rules['insufficient'] = '用户的信息不足判断条件。'
        rules['topics'][0]['bailian_criteria'] = {'true': '用户的命中条件。', 'false': '用户的排除条件。'}
        calls = []
        def chat(_key, payload):
            calls.append(payload)
            return {'choices': [{'message': {'content': '{"hits":[],"insufficient":false}'}}]}
        result = deepseek_review.request_review([{'summary': '虚构凭证'}], rules,
                                               'fake', threading.Event(), chat_fn=chat)
        self.assertEqual(result['status'], 'success')
        content = calls[0]['messages'][0]['content']
        for prompt in (rules['common'], rules['insufficient'], '用户的命中条件。', '用户的排除条件。'):
            self.assertIn(prompt, content)
        topics = json.loads(content.split('本次问题：\n')[1])
        self.assertEqual({t['id']: t['question'] for t in topics},
                         {key: q.instructions[len(rules['common']) + 2:]
                          for key, q in questions_for(rules).items() if key != 'insufficient'})

    def test_numerical_settings_are_not_evaluated_or_sent(self):
        config = load_config(self.store)
        save_config(self.store, {**config, 'amount_yuan': 1, 'net_asset_pct': 0,
                                'asset_pct': 0, 'revenue_pct': 0, 'percentile': .99})
        with patch('ledger_viewer.dd_features.FeatureContext', side_effect=AssertionError('numerical screening')):
            batch = self.run_job({'review_accounts': ['其他应付款']})
        self.assertEqual(len(voucher_results(self.store, batch)), 1)
        for state, _questions in fixtures.JevClient.calls:
            self.assertEqual(state['features']['facts'], [])
            self.assertNotIn('percentile', json.dumps(state))
        data = json.loads(self.store.db.execute('SELECT features_json FROM jev_results WHERE batch_id=? LIMIT 1', (batch,)).fetchone()[0])
        self.assertEqual(data['rule_categories'], [])

    def test_choice_verdict_is_used_even_with_zero_confidence(self):
        batch = self.run_job({'review_accounts': ['其他应付款']})
        group = voucher_results(self.store, batch)[0]
        self.assertEqual([category[0] for category in group['categories']], ['OP01'])
        self.assertIsNone(group['score'])
        self.assertIsNone(group['categories'][0][3])
        data = json.loads(group['hits'][0]['response_json'])
        self.assertEqual(data['answers']['OP01'], {'type': 'choice', 'choice': 'hit'})
        self.assertNotIn('confidence', data['answers']['OP01'])

    def test_probability_only_or_unknown_verdict_is_rejected(self):
        rules = rules_for_accounts(default_rules(), ['其他应付款'])
        answers = {key: fixtures.decision_answer() for key in [*(t['id'] for t in rules['topics']), 'insufficient']}
        raw = {'model': rules['model'], 'answers': answers, 'usage': {'input_tokens': 1, 'output_tokens': 0}}
        for answer in ({'type': 'noul', 'noul': .99}, {'type': 'choice', 'choice': 'maybe'}):
            invalid = copy.deepcopy(raw)
            invalid['answers']['OP01'] = answer
            with self.assertRaises(ValueError):
                validate_response(invalid, rules)

    def test_multiple_chunks_union_explicit_hits_and_information_gaps(self):
        rules = rules_for_accounts(default_rules(), ['其他应付款'])
        calls = []
        class Client:
            def __init__(self, **options):
                self.model = options['model']
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def system_one(self, *, state, questions):
                calls.append(state)
                first = len(calls) == 1
                return SimpleNamespace(model_dump=lambda **_kwargs: {
                    'model': self.model,
                    'answers': {key: fixtures.decision_answer(first and key in ('OP01', 'insufficient')) for key in questions},
                    'usage': {'input_tokens': 1, 'output_tokens': 0}})
        result = request_review([{'name': '其他应付款', 'summary': '长摘要' * 1200}], rules,
                                'test', threading.Event(), Client)
        self.assertEqual(result['status'], 'success')
        self.assertGreater(len(calls), 1)
        data = result['items'][0]
        self.assertEqual(data['answers']['OP01']['choice'], 'hit')
        self.assertEqual(data['answers']['insufficient']['choice'], 'hit')
        self.assertEqual(data['answers']['OP02']['choice'], 'no_hit')

    def test_historical_specialist_batch_keeps_original_rules_and_results(self):
        batch = prepare_batch(self.store, {'review_accounts': ['其他应付款']})
        info = batch_info(self.store, batch)
        rules = json.loads(info['body_json'])
        rules.pop('decision_mode')
        rules['thresholds'] = {'谨慎': 50, '中': 65, '宽松': 85}
        rules['bailian_thresholds'] = dict(rules['thresholds'])
        answers = {key: {'type': 'noul', 'noul': .9 if key == 'OP01' else .01}
                   for key in [*(t['id'] for t in rules['topics']), 'insufficient']}
        raw = json.dumps({'model': rules['model'], 'answers': answers})
        with self.store.db:
            self.store.db.execute('UPDATE jev_rules SET body_json=? WHERE id=?', (json.dumps(rules), info['rule_id']))
            self.store.db.execute("UPDATE jev_results SET status='success',response_json=?,score=90,insufficient=0 WHERE batch_id=?", (raw, batch))
        group = voucher_results(self.store, batch)[0]
        self.assertIn('OP01', [item[0] for item in group['categories']])
        self.assertEqual(json.loads(batch_info(self.store, batch)['body_json']), rules)


if __name__ == '__main__':
    unittest.main()
