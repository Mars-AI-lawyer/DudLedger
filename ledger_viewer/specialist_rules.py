"""Account-specific review policy, separate from historical general review rules."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

PROFILE = 'account-special-v1'
ACCOUNTS = ('其他应收款', '其他应付款', '实收资本', '资本公积', '营业外支出', '财务费用')


def default_rules():
    return json.loads(Path(__file__).with_name('specialist_rules.json').read_text(encoding='utf-8'))


def validate_rules(rules):
    defaults = default_rules()
    if rules.get('profile') != PROFILE or rules.get('model') not in ('jev-1.13.0', 'jev-1.14.0'):
        raise ValueError('专项规则模型须为 Jev 1.13 或 1.14')
    if [t.get('id') for t in rules.get('topics', [])] != [t['id'] for t in defaults['topics']]:
        raise ValueError('专项规则须保留完整的 30 项问题编号')
    for topic, original in zip(rules['topics'], defaults['topics']):
        if topic.get('account') != original['account'] or topic.get('category') != original['category']:
            raise ValueError('专项问题的科目及建议分类不可更改')
        if any(not isinstance(topic.get(k), str) or not topic[k].strip() for k in ('label', 'question', 'advice')):
            raise ValueError('专项问题、名称及建议不能为空')
        if 'bailian_criteria' in topic:
            criteria = topic['bailian_criteria']
            if not isinstance(criteria, dict) or set(criteria) != {'true', 'false'} or any(not isinstance(value, str) or not value.strip() for value in criteria.values()):
                raise ValueError('百炼正反判断条件不能为空')
    if any(not isinstance(rules.get(k), str) or not rules[k].strip() for k in ('common', 'insufficient')):
        raise ValueError('基础提示词及信息不足提问不能为空')
    for field, label in (('thresholds', 'Jev'), ('bailian_thresholds', '百炼')):
        values = rules.get(field, {})
        if set(values) != {'谨慎', '中', '宽松'} or any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values()):
            raise ValueError(f'需要三个有效的{label}阈值')
        if not 0 <= values['谨慎'] < values['中'] < values['宽松'] <= 100:
            raise ValueError(f'{label}阈值必须满足 0 ≤ 谨慎 < 中 < 宽松 ≤ 100')


def save_rules(store, rules):
    validate_rules(rules)
    with store.db:
        return store.db.execute('INSERT INTO jev_rules(body_json) VALUES(?)',
            (json.dumps(rules, ensure_ascii=False, allow_nan=False),)).lastrowid


def load_rules(store):
    row = store.db.execute("SELECT id,body_json FROM jev_rules WHERE json_extract(body_json,'$.profile')=? ORDER BY id DESC LIMIT 1", (PROFILE,)).fetchone()
    if row:
        rules = json.loads(row['body_json'])
        changed = False
        if 'bailian_thresholds' not in rules:
            rules['bailian_thresholds'] = default_rules()['bailian_thresholds']
            changed = True
        defaults = {topic['id']: topic for topic in default_rules()['topics']}
        for topic in rules['topics']:
            if 'bailian_criteria' in defaults[topic['id']] and 'bailian_criteria' not in topic:
                topic['bailian_criteria'] = copy.deepcopy(defaults[topic['id']]['bailian_criteria'])
                changed = True
        if changed:
            return save_rules(store, rules), rules
        return row['id'], rules
    rules = default_rules()
    return save_rules(store, rules), rules


def selected_accounts(values=None):
    values = list(values or ACCOUNTS)
    if any(value not in ACCOUNTS for value in values):
        raise ValueError('请从六类专项科目中选择审查范围')
    return [name for name in ACCOUNTS if name in values]


def account_for(row, prefixes=None):
    name = re.sub(r'^[\d\s.\-]+', '', row['name'] or '').strip()
    for account in ACCOUNTS:
        if name.startswith(account):
            return account
    if name == '股本' or name.startswith(('股本/', '股本／', '股本_')):
        return '实收资本'
    prefixes = prefixes if prefixes is not None else {'1221': '其他应收款', '2241': '其他应付款',
        '4001': '实收资本', '4002': '资本公积', '6711': '营业外支出', '6603': '财务费用'}
    code = row['code'] or ''
    for prefix in sorted(prefixes, key=len, reverse=True):
        if code.startswith(prefix):
            return prefixes[prefix]
    return None


def account_prefixes(store):
    prefixes = {'1221': '其他应收款', '2241': '其他应付款', '4001': '实收资本',
                '4002': '资本公积', '6711': '营业外支出', '6603': '财务费用'}
    for row in store.db.execute('SELECT code,name FROM accounts'):
        if row['code']:
            named = account_for(row, {})
            if named:
                prefixes[row['code']] = named
            elif row['code'] in prefixes:
                prefixes.pop(row['code'])
    return prefixes


def rules_for_accounts(rules, accounts):
    adapted = copy.deepcopy(rules)
    adapted['topics'] = [topic for topic in rules['topics'] if topic['account'] in accounts]
    adapted.pop('review', None)
    return adapted
