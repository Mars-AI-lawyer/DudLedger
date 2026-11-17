"""Account-specific review policy, separate from historical general review rules."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from .jev_runtime import MODEL

PROFILE = 'account-special-v1'
NEW_TEMPLATE_ACCOUNTS = ('预收账款', '合同负债', '应收票据', '其他业务收入', '其他业务成本',
                         '营业外收入', '投资收益', '资产处置损益')
ACCOUNTS = ('其他应收款', '其他应付款', '实收资本', '资本公积', '营业外支出', '财务费用',
            *NEW_TEMPLATE_ACCOUNTS)
TEMPLATE_REVISION = 2
DEFAULT_PREFIXES = {'1221': '其他应收款', '2241': '其他应付款', '4001': '实收资本',
                    '4002': '资本公积', '6711': '营业外支出', '6603': '财务费用',
                    '2203': '预收账款', '2205': '合同负债', '1121': '应收票据',
                    '6051': '其他业务收入', '6402': '其他业务成本', '6301': '营业外收入',
                    '6111': '投资收益', '6115': '资产处置损益'}


def default_rules():
    return json.loads(Path(__file__).with_name('specialist_rules.json').read_text(encoding='utf-8'))


def topic_question(topic):
    """Preserve legacy provider conditions as part of the shared prompt."""
    question = topic['question']
    criteria = topic.get('bailian_criteria', {})
    for key, label in (('true', '补充命中条件'), ('false', '排除条件')):
        if criteria.get(key):
            question += '\n' + label + '：' + criteria[key]
    return question


def migrate_rules(rules):
    rules = copy.deepcopy(rules)
    rules['model'] = MODEL
    rules['decision_mode'] = 'prompt'
    rules.pop('thresholds', None)
    rules.pop('bailian_thresholds', None)
    for topic in rules['topics']:
        topic['question'] = topic_question(topic)
        topic.pop('bailian_criteria', None)
        topic.pop('criteria_mode', None)
    if rules.get('template_revision', 1) < TEMPLATE_REVISION:
        # Add new account drafts once; never restore deleted old accounts or
        # replace prompts for accounts the user has already configured.
        existing_accounts = set(review_accounts(rules))
        identifiers = {topic['id'] for topic in rules['topics']}
        labels = {topic['label'] for topic in rules['topics']}
        for topic in default_rules()['topics']:
            if topic['account'] not in NEW_TEMPLATE_ACCOUNTS or topic['account'] in existing_accounts:
                continue
            identifier, label = topic['id'], topic['label']
            suffix = 1
            while topic['id'] in identifiers or topic['label'] in labels:
                topic['id'] = f'{identifier}_draft{suffix}'
                topic['label'] = f'{label}（{topic["account"]}·{suffix}）'
                suffix += 1
            rules['topics'].append(topic)
            identifiers.add(topic['id'])
            labels.add(topic['label'])
        rules['template_revision'] = TEMPLATE_REVISION
    return rules


def validate_rules(rules):
    if rules.get('profile') != PROFILE or rules.get('model') != MODEL:
        raise ValueError('专项规则模型固定为 Jev 1.14')
    topics = rules.get('topics')
    if not isinstance(topics, list) or not topics:
        raise ValueError('请至少保留一个审查条目')
    identifiers, labels = set(), set()
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError('审查条目格式无效')
        identifier = topic.get('id', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', identifier) or identifier in identifiers or identifier in ('review', 'insufficient'):
            raise ValueError('审查条目编号须为唯一的英文或数字编号')
        identifiers.add(identifier)
        if any(not isinstance(topic.get(k), str) or not topic[k].strip() for k in ('account', 'category', 'label', 'question')):
            raise ValueError('科目、条目名称和审查提示词不能为空')
        if not isinstance(topic.get('advice'), str):
            raise ValueError('核查建议须为文字，可留空')
        if topic['label'] in labels or topic['label'] in ('全部', '待分类'):
            raise ValueError('条目名称不能重复，也不能使用“全部”或“待分类”')
        labels.add(topic['label'])
        if 'bailian_criteria' in topic or 'criteria_mode' in topic:
            raise ValueError('判断条件请统一写入审查提示词')
    codes = rules.get('account_codes', {})
    if not isinstance(codes, dict) or any(
        name not in review_accounts(rules) or not isinstance(values, list) or
        any(not isinstance(code, str) or not re.fullmatch(r'[0-9A-Za-z.\-]+', code) for code in values)
        for name, values in codes.items()
    ):
        raise ValueError('科目编码须为英文或数字，可用逗号分隔多个编码')
    all_codes = [code for values in codes.values() for code in values]
    if len(all_codes) != len(set(all_codes)):
        raise ValueError('同一个科目编码不能分配给多个审查科目')
    if any(not isinstance(rules.get(k), str) or not rules[k].strip() for k in ('common', 'insufficient')):
        raise ValueError('基础提示词及信息不足提问不能为空')
    if rules.get('decision_mode') != 'prompt':
        raise ValueError('专项审查须按提示词直接判断')


def save_rules(store, rules):
    rules = migrate_rules(rules)
    validate_rules(rules)
    with store.db:
        return store.db.execute('INSERT INTO jev_rules(body_json) VALUES(?)',
            (json.dumps(rules, ensure_ascii=False, allow_nan=False),)).lastrowid


def load_rules(store):
    row = store.db.execute("SELECT id,body_json FROM jev_rules WHERE json_extract(body_json,'$.profile')=? ORDER BY id DESC LIMIT 1", (PROFILE,)).fetchone()
    if row:
        stored = json.loads(row['body_json'])
        rules = migrate_rules(stored)
        if rules != stored:
            return save_rules(store, rules), rules
        return row['id'], rules
    rules = default_rules()
    return save_rules(store, rules), rules


def review_accounts(rules=None):
    return tuple(dict.fromkeys(topic['account'] for topic in rules['topics'])) if rules is not None else ACCOUNTS


def selected_accounts(values=None, rules=None):
    available = review_accounts(rules)
    values = list(values or available)
    if any(value not in available for value in values):
        raise ValueError('请从已设置提示词的科目中选择审查范围')
    return [name for name in available if name in values]


def account_for(row, prefixes=None, accounts=None):
    name = re.sub(r'^[\d\s.\-]+', '', row['name'] or '').strip()
    available = accounts if accounts is not None else ACCOUNTS
    for account in sorted(available, key=len, reverse=True):
        if name.startswith(account):
            return account
    if '实收资本' in available and (name == '股本' or name.startswith(('股本/', '股本／', '股本_'))):
        return '实收资本'
    prefixes = prefixes if prefixes is not None else DEFAULT_PREFIXES
    code = row['code'] or ''
    for prefix in sorted(prefixes, key=len, reverse=True):
        if code.startswith(prefix):
            if prefixes[prefix] in available:
                return prefixes[prefix]
    return None


def account_prefixes(store, rules=None):
    prefixes = DEFAULT_PREFIXES.copy()
    available = review_accounts(rules)
    prefixes = {code: name for code, name in prefixes.items() if name in available}
    for row in store.db.execute('SELECT code,name FROM accounts'):
        if row['code']:
            named = account_for(row, {}, available)
            if named:
                prefixes[row['code']] = named
            elif row['code'] in prefixes:
                prefixes.pop(row['code'])
    for name, codes in (rules or {}).get('account_codes', {}).items():
        for code in codes:
            prefixes[code] = name
    return prefixes


def rules_for_accounts(rules, accounts):
    adapted = copy.deepcopy(rules)
    adapted['topics'] = [topic for topic in rules['topics'] if topic['account'] in accounts]
    adapted.pop('review', None)
    return adapted
