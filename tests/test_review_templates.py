"""Offline checks for expanded account drafts and non-destructive upgrades."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_specialist_review as fixtures
from ledger_viewer import deepseek_review
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.jev_review import batch_info, prepare_batch, request_review
from ledger_viewer.jev_ui import ReviewPanel
from ledger_viewer.specialist_rules import (
    ACCOUNTS, DEFAULT_PREFIXES, NEW_TEMPLATE_ACCOUNTS, account_for,
    default_rules, load_rules, review_accounts, rules_for_accounts, save_rules,
    validate_rules,
)


class ReviewTemplateTest(unittest.TestCase):
    setUp = fixtures.SpecialistReviewTest.setUp
    tearDown = fixtures.SpecialistReviewTest.tearDown

    def legacy_rules(self):
        rules = default_rules()
        rules.pop('template_revision')
        rules['topics'] = [t for t in rules['topics'] if t['account'] not in NEW_TEMPLATE_ACCOUNTS]
        return rules

    def insert_legacy(self, rules):
        with self.store.db:
            return self.store.db.execute('INSERT INTO jev_rules(body_json) VALUES(?)',
                                         (json.dumps(rules),)).lastrowid

    def test_upgrade_preserves_customizations_and_batch_history_and_runs_once(self):
        batch = prepare_batch(self.store, {'review_accounts': ['其他应付款']})
        old_version = batch_info(self.store, batch)['rule_id']
        legacy = self.legacy_rules()
        legacy['common'] = '用户已调整的通用边界。'
        legacy['topics'][0]['question'] = '用户已调整的股东往来问题。'
        legacy['topics'] = [t for t in legacy['topics'] if t['account'] != '资本公积']
        legacy['account_codes'] = {'其他应收款': ['CUSTOM1221']}
        with self.store.db:
            self.store.db.execute('UPDATE jev_rules SET body_json=? WHERE id=?',
                                  (json.dumps(legacy), old_version))
        version, upgraded = load_rules(self.store)
        self.assertGreater(version, old_version)
        self.assertEqual(upgraded['common'], legacy['common'])
        self.assertEqual(upgraded['account_codes'], legacy['account_codes'])
        self.assertEqual(upgraded['topics'][:len(legacy['topics'])], legacy['topics'])
        self.assertNotIn('资本公积', review_accounts(upgraded))
        self.assertTrue(set(NEW_TEMPLATE_ACCOUNTS) <= set(review_accounts(upgraded)))
        self.assertEqual(load_rules(self.store), (version, upgraded))
        self.assertEqual(json.loads(batch_info(self.store, batch)['body_json']), legacy)
        upgraded['topics'] = [t for t in upgraded['topics'] if t['account'] != '预收账款']
        saved = save_rules(self.store, upgraded)
        self.assertEqual(load_rules(self.store)[0], saved)
        self.assertNotIn('预收账款', review_accounts(load_rules(self.store)[1]))

    def test_upgrade_preserves_existing_new_account_and_resolves_custom_id_collisions(self):
        legacy = self.legacy_rules()
        custom = {'id': 'MY_CL', 'account': '合同负债', 'category': '自定义',
                  'label': '用户合同问题', 'question': '用户自定合同问题？', 'advice': ''}
        legacy['topics'].append(custom)
        draft = next(t for t in default_rules()['topics'] if t['account'] == '预收账款')
        legacy['topics'].append({**custom, 'id': draft['id'], 'label': draft['label'], 'account': '用户科目'})
        self.insert_legacy(legacy)
        _, upgraded = load_rules(self.store)
        validate_rules(upgraded)
        self.assertEqual([t for t in upgraded['topics'] if t['account'] == '合同负债'], [custom])
        self.assertEqual([t for t in upgraded['topics'] if t['account'] == '用户科目'], legacy['topics'][-1:])
        new = [t for t in upgraded['topics'] if t['account'] == '预收账款']
        self.assertEqual(len(new), 4)
        self.assertNotEqual(new[0]['id'], draft['id'])
        self.assertEqual(new[0]['question'], draft['question'])
        self.assertEqual(load_rules(self.store)[1], upgraded)

    def test_new_accounts_match_names_codes_and_exclude_broad_accounts(self):
        rules = default_rules()
        validate_rules(rules)
        self.assertEqual(review_accounts(rules), ACCOUNTS)
        for account in NEW_TEMPLATE_ACCOUNTS:
            with self.subTest(account=account):
                code = next(k for k, v in DEFAULT_PREFIXES.items() if v == account)
                self.assertEqual(account_for({'name': account + '_客户甲', 'code': ''}), account)
                self.assertEqual(account_for({'name': '客户甲', 'code': code + '01'}), account)
                topics = rules_for_accounts(rules, [account])['topics']
                self.assertEqual(len(topics), 4)
                for topic in topics:
                    self.assertIn('命中条件：', topic['question'])
                    self.assertIn('排除条件：', topic['question'])
        for code, name in [('1002', '银行存款'), ('1405', '存货'), ('1122', '应收账款'),
                           ('2202', '应付账款'), ('6001', '主营业务收入')]:
            self.assertNotIn(name, review_accounts(rules))
            self.assertIsNone(account_for({'name': name, 'code': code}))
        self.assertIsNone(account_for({'name': '客户甲', 'code': '112101'}, accounts=['其他应付款']))

    def test_each_new_account_selection_uses_only_its_checks_and_complete_voucher(self):
        rows = ['日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额']
        for index, account in enumerate(NEW_TEMPLATE_ACCOUNTS, 1):
            code = next(k for k, v in DEFAULT_PREFIXES.items() if v == account)
            rows.extend([f'2026-02-01,{100 + index},{code},{account},虚构专项交易,0,100',
                         f'2026-02-01,{100 + index},1002,银行存款,虚构专项交易,100,0'])
        source = self.base / 'expanded.csv'
        source.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        header, _, mapping = guess_header(source, 'CSV', 'entry')
        self.store.import_sheet(ImportSpec(source, 'CSV', header, 'entry', mapping))
        for account in NEW_TEMPLATE_ACCOUNTS:
            with self.subTest(account=account):
                batch = prepare_batch(self.store, {'review_accounts': [account]})
                info = batch_info(self.store, batch)
                self.assertEqual(json.loads(info['scope_json'])['review_accounts'], [account])
                results = self.store.db.execute('SELECT * FROM jev_results WHERE batch_id=?', (batch,)).fetchall()
                self.assertEqual(len(results), 2)
                self.assertEqual({json.loads(r['local_json'])['name'] for r in results}, {account, '银行存款'})
                feature = json.loads(results[0]['features_json'])
                self.assertEqual(feature['review_accounts'], [account])
                self.assertEqual(feature['check_ids'], [t['id'] for t in default_rules()['topics'] if t['account'] == account])

    def test_new_prompts_are_shared_by_all_providers_after_user_edit(self):
        for account in NEW_TEMPLATE_ACCOUNTS:
            with self.subTest(account=account):
                rules = rules_for_accounts(default_rules(), [account])
                rules['topics'][0]['question'] += ' 用户补充：按明确的争议线索判断。'
                saved = save_rules(self.store, rules)
                self.assertEqual(load_rules(self.store), (saved, rules))
                calls = []
                class Client:
                    def __init__(self, **options):
                        self.model = options['model']
                    def __enter__(self):
                        return self
                    def __exit__(self, *_args):
                        pass
                    def system_one(self, *, state, questions):
                        calls.append(questions)
                        return SimpleNamespace(model_dump=lambda **_kwargs: {
                            'model': self.model,
                            'answers': {k: fixtures.decision_answer(k == rules['topics'][0]['id']) for k in questions},
                            'usage': {'input_tokens': 1, 'output_tokens': 0}})
                for model in ('jev-1.14.0', 'decision-model-preview'):
                    provider_rules = copy.deepcopy(rules)
                    provider_rules['model'] = model
                    result = request_review([{'name': account, 'summary': '虚构专项交易'}],
                                            provider_rules, 'fictional', threading.Event(), Client)
                    self.assertEqual(result['status'], 'success')
                self.assertEqual({k: v.model_dump() for k, v in calls[0].items()},
                                 {k: v.model_dump() for k, v in calls[1].items()})
                payloads = []
                def chat(_key, payload):
                    payloads.append(payload)
                    return {'choices': [{'message': {'content': '{"hits":[],"insufficient":false}'}}]}
                result = deepseek_review.request_review([{'name': account, 'summary': '虚构专项交易'}],
                                                       rules, 'fictional', threading.Event(), chat_fn=chat)
                self.assertEqual(result['status'], 'success')
                for topic in rules['topics']:
                    self.assertIn(topic['question'], calls[0][topic['id']].instructions)
                    self.assertIn(topic['question'], payloads[0]['messages'][0]['content'])

    def test_headless_checkbox_refresh_and_batch_selection_include_new_accounts(self):
        panel = Mock()
        panel.app.store = self.store
        panel.app.import_busy = panel.preparing = False
        panel.account_selection.winfo_children.return_value = []
        panel.review_accounts = {'其他应付款': Mock(get=lambda: True)}
        def boolean_var(*, value):
            return SimpleNamespace(get=lambda: value)
        with patch('ledger_viewer.jev_ui.tk.BooleanVar', side_effect=boolean_var), \
             patch('ledger_viewer.jev_ui.ttk.Checkbutton') as checkbox:
            ReviewPanel.refresh_accounts(panel)
        self.assertEqual(tuple(panel.review_accounts), ACCOUNTS)
        self.assertEqual(checkbox.call_count, len(ACCOUNTS))
        self.assertTrue(panel.review_accounts['其他应付款'].get())
        self.assertTrue(all(not panel.review_accounts[name].get() for name in NEW_TEMPLATE_ACCOUNTS))
        panel.review_accounts = {name: SimpleNamespace(get=lambda name=name: name in ('预收账款', '其他业务成本'))
                                 for name in ACCOUNTS}
        ReviewPanel.new_batch(panel)
        panel.prepare.assert_called_once_with({'review_accounts': ['预收账款', '其他业务成本']})


if __name__ == '__main__':
    unittest.main()
