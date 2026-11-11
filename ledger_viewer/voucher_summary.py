"""Shared, paged voucher presentation without changing durable voucher keys."""

import json
from collections import defaultdict

from .importer import format_money
from .voucher_notes import source_account, voucher_signature


def voucher_lines_many(store, keys):
    result = defaultdict(list)
    keys = tuple(dict.fromkeys(keys))
    for offset in range(0, len(keys), 100):
        requested = []
        for key in keys[offset:offset + 100]:
            try:
                parts = json.loads(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(parts, list) or not parts:
                continue
            if (len(parts), parts[0]) not in ((5, 'period'), (4, 'date'), (4, 'row')):
                continue
            requested.append((key, *parts, *([''] * (5 - len(parts)))))
        if not requested:
            continue
        values = ','.join('(?,?,?,?,?,?)' for _ in requested)
        base = '''SELECT k.vkey,e.*,a.code,a.name,s.filename,s.sheet,s.original_path,s.mapping_json
            FROM requested k JOIN entries e ON {condition}
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id {extra}'''
        queries = [base.format(condition="k.kind='period' AND e.fiscal_year=k.p1 AND e.period=k.p2 AND e.voucher_type=k.p3 AND e.voucher_no=k.p4", extra=''),
                   base.format(condition="k.kind='date' AND e.entry_date=k.p1 AND e.voucher_type=k.p2 AND e.voucher_no=k.p3 AND (e.fiscal_year='' OR e.period='')", extra=''),
                   base.format(condition="k.kind='row' AND e.row_no=k.p3", extra='WHERE s.original_path=k.p1 AND s.sheet=k.p2')]
        sql = 'WITH requested(vkey,kind,p1,p2,p3,p4) AS (VALUES ' + values + ') ' + ' UNION ALL '.join(queries) + ' ORDER BY source_id,row_no,id'
        for row in store.db.execute(sql, [value for item in requested for value in item]):
            result[row['vkey']].append(dict(row))
    return result


def summarize(lines):
    dates = sorted({row['entry_date'] for row in lines if row['entry_date']})
    summaries = list(dict.fromkeys(row['summary'] for row in lines if row['summary']))
    names = []
    for row in lines:
        name = source_account(row)[1]
        if not name or name.replace('.', '').isdigit():
            name = row['name']
        if not name or name.replace('.', '').isdigit():
            name = '科目名称缺失'
        if name not in names:
            names.append(name)
    debit = sum(row['debit_cents'] for row in lines)
    credit = sum(row['credit_cents'] for row in lines)
    first = lines[0] if lines else {}
    return {'date': ('—' if not dates else dates[0] if len(dates) == 1 else f'{dates[0]}—{dates[-1]}'),
            'voucher': f"{first.get('voucher_type', '')}{first.get('voucher_no', '')}" or '无凭证号',
            'summary': '；'.join(summaries), 'accounts': '、'.join(names) or '科目名称缺失',
            'amount': '—' if not lines else format_money(debit, comma=True) if debit == credit else f'借 {format_money(debit, comma=True)}／贷 {format_money(credit, comma=True)}',
            'amount_status': '' if debit == credit else '已导入借贷不一致',
            'debit_cents': debit, 'credit_cents': credit}


def summaries_for_keys(store, keys):
    keys = tuple(dict.fromkeys(keys))
    live = voucher_lines_many(store, keys)
    references = {}
    for offset in range(0, len(keys), 100):
        batch = keys[offset:offset + 100]
        sql = 'SELECT * FROM dd_voucher_refs WHERE voucher_key IN (' + ','.join('?' for _ in batch) + ')'
        references.update((row['voucher_key'], row) for row in store.db.execute(sql, batch))
    result = {}
    for key in keys:
        lines = live.get(key, [])
        ref = references.get(key)
        status = '正常'
        if not lines:
            status = '原凭证未匹配（归集时快照）' if ref else '原凭证未匹配'
        elif ref and voucher_signature(lines) != ref['signature']:
            status = '内容变化，待复核'
        snapshot = json.loads(ref['snapshot_json']) if ref and not lines else []
        result[key] = {**summarize(lines or snapshot), 'key': key, 'status': status,
                       'entry_id': lines[0]['id'] if lines else None}
        if not lines and not snapshot:
            try:
                parts = json.loads(key)
                if isinstance(parts, list) and len(parts) in (4, 5) and parts[0] in ('period', 'date'):
                    result[key]['voucher'] = f'{parts[-2]}{parts[-1]}'
            except (TypeError, ValueError):
                pass
    return result


def read_marks(store):
    from .voucher_notes import list_marks
    marks = list_marks(store)
    missing = summaries_for_keys(store, [mark['key'] for mark in marks if not mark['lines']])
    summaries = {}
    for mark in marks:
        lines = mark['lines']
        summaries[mark['key']] = ({**summarize(lines), 'entry_id': lines[0]['id'], 'status': mark['status']}
                                  if lines else missing[mark['key']])
    return marks, summaries
