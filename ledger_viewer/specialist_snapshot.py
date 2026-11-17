"""Local snapshots for selected-account vouchers and bounded historical context."""
from __future__ import annotations

import json

from .analysis_api import iter_entries
from .background import check_cancelled, cooperate
from .dd_features import closing_voucher
from .related_parties import review_party_context, role_grouped_names
from .review_providers import preferred_provider
from .specialist_rules import account_for, account_prefixes, load_rules, selected_accounts, review_accounts
from .voucher_notes import voucher_key


def prepare_batch(store, scope, *, cancelled=None, progress=None):
    from .jev_review import digest, encode, fingerprint, send_fields
    version, rules = load_rules(store)
    from .bailian_review import DEFAULT_ENDPOINT, validate_endpoint
    endpoint = validate_endpoint(scope.get('bailian_endpoint', DEFAULT_ENDPOINT))
    preferred = preferred_provider(scope.get('preferred_provider'))
    accounts = selected_accounts(scope.get('review_accounts'), rules)
    available = review_accounts(rules)
    prefixes = account_prefixes(store, rules)
    filters = {k: v for k, v in scope.items() if k in ('account', 'search', 'fiscal_year', 'period', 'include_descendants', 'source_ids')}
    keys, relevant_parties = set(), set()
    for index, row in enumerate(iter_entries(store, **filters)):
        if cancelled is not None and index % 250 == 0:
            cooperate(cancelled)
        if account_for(row, prefixes, available) in accounts:
            keys.add(voucher_key(row))
            if row['counterparty']:
                relevant_parties.add(row['counterparty'].strip())
    if not keys:
        raise ValueError('选定范围没有待审查科目的凭证，请检查科目选择、名称或编码设置')
    parties = review_party_context(store.related_parties())
    # One local pass provides complete vouchers and exact-counterparty history.
    groups, history_index = {}, {}
    for index, row in enumerate(iter_entries(store)):
        if cancelled is not None and index % 250 == 0:
            cooperate(cancelled)
        key = voucher_key(row)
        if key in keys:
            groups.setdefault(key, []).append(row)
        party = (row['counterparty'] or '').strip()
        if party in relevant_parties:
            history_index.setdefault(party, []).append(row)
    saved_scope = {**filters, 'review_accounts': accounts, 'profile': rules['profile'],
                   'preferred_provider': preferred, 'bailian_endpoint': endpoint,
                   'bailian_free_confirmed': scope.get('bailian_free_confirmed') is True,
                   'data_revision': store.setting('data_revision', '0'),
                   'party_context_version': 1,
                   'party_hash': digest(parties)}
    hashes = []
    with store.db:
        batch = store.db.execute('INSERT INTO jev_batches(rule_id,model,scope_json,snapshot_hash) VALUES(?,?,?,?)',
            (version, 'decision-model-preview' if preferred == 'bailian' else rules['model'], encode(saved_scope), '')).lastrowid
        for number, (key, rows) in enumerate(groups.items(), 1):
            if cancelled is not None:
                check_cancelled(cancelled)
            active = [name for name in accounts if any(account_for(row, prefixes, available) == name for row in rows)]
            feature = {'decision_mode': 'prompt', 'facts': [], 'rule_categories': []}
            feature['review_accounts'] = active
            feature['check_ids'] = [t['id'] for t in rules['topics'] if t['account'] in active]
            history = []
            seen = set()
            # Exact same counterparty, 90-day window, same currency and amount.
            # These are candidate records, never a finding of capital withdrawal.
            if any(name in active for name in ('实收资本', '其他应收款', '其他应付款')):
                from datetime import date
                for row in rows:
                    if not row['counterparty'] or not row['entry_date']:
                        continue
                    try:
                        day = date.fromisoformat(row['entry_date'])
                    except ValueError:
                        continue
                    amount = max(abs(row['debit_cents']), abs(row['credit_cents']))
                    for previous in history_index.get(row['counterparty'].strip(), []):
                        if previous['id'] in seen or voucher_key(previous) == key or previous['currency'] != row['currency']:
                            continue
                        try:
                            distance = (date.fromisoformat(previous['entry_date']) - day).days
                        except (ValueError, TypeError):
                            continue
                        if abs(distance) <= 90 and amount and max(abs(previous['debit_cents']), abs(previous['credit_cents'])) == amount:
                            seen.add(previous['id'])
                            history.append({**send_fields(previous), 'day_offset': distance,
                                'match_basis': '往来单位文字、币种及分录金额相同；不代表实际资金流向或未清偿'})
            total = len(history)
            history = history[:20]
            feature['background'] = {'historical_transactions': history, 'history_candidates': total,
                'history_truncated': total > len(history),
                'limits': '未提供账龄及完整银行流水；历史为同名往来单位90日内同金额候选，不证明资金流转、未清偿或关联关系。不得自行推断长期挂账或抽逃出资。'}
            payloads = [send_fields(row) for row in rows]
            base = digest(['account-special-v1', payloads, parties, feature, version, rules, endpoint])
            for index, (row, payload) in enumerate(zip(rows, payloads)):
                fp = fingerprint(row, payload)
                hashes.append(fp)
                store.db.execute('''INSERT INTO jev_results(batch_id,entry_id,voucher_key,local_json,input_json,fingerprint,cache_key,features_json,status,error)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (batch, row['id'], key, encode(row), encode(payload), fp,
                    digest([base, index]), encode(feature), 'skipped' if closing_voucher(rows) else 'pending',
                    '结转损益凭证，未送 AI' if closing_voucher(rows) else ''))
            if progress and number % 100 == 0:
                progress(f'已整理 {number:,} 张专项凭证及完整分录')
        store.db.execute('UPDATE jev_batches SET snapshot_hash=? WHERE id=?',
                         (digest({'entries': hashes, 'scope': saved_scope}), batch))
    return batch


def snapshot_current(store, info, *, check_entries=True):
    from .jev_review import digest, fingerprint, send_fields
    scope = json.loads(info['scope_json'])
    if scope['data_revision'] != store.setting('data_revision', '0'):
        return False
    parties = (review_party_context(store.related_parties()) if scope.get('party_context_version') == 1
               else role_grouped_names(store.related_parties()))
    if scope['party_hash'] != digest(parties):
        return False
    if not check_entries:
        return True
    rows = store.db.execute('''SELECT e.*,a.code,a.name,s.sha256,s.original_path,s.sheet FROM jev_results r
        JOIN entries e ON e.id=r.entry_id JOIN accounts a ON a.key=e.account_key
        JOIN sources s ON s.id=e.source_id WHERE r.batch_id=? ORDER BY r.id''', (info['id'],)).fetchall()
    hashes = [fingerprint(row, send_fields(row)) for row in rows]
    return digest({'entries': hashes, 'scope': scope}) == info['snapshot_hash']
