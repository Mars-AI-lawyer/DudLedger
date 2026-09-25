"""Consent-bound snapshots, bounded Jev jobs, and evidence-linked review results."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from queue import Queue

from typesafe_sdk import Noul, Score, TypeSafeClient, RetryPolicy

from .analysis_api import iter_entries
from .importer import format_money
from .jev_rules import load_rules
from .related_parties import related_party_match
from .voucher_notes import voucher_key
from .jev_runtime import REQUEST_SLOTS

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jev_rules (
 id INTEGER PRIMARY KEY, body_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jev_batches (
 id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES jev_rules(id), model TEXT NOT NULL,
 scope_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'preview',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_jev_one_running ON jev_batches(status) WHERE status='running';
CREATE TABLE IF NOT EXISTS jev_results (
 id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES jev_batches(id),
 entry_id INTEGER NOT NULL, voucher_key TEXT NOT NULL, local_json TEXT NOT NULL,
 input_json TEXT NOT NULL, fingerprint TEXT NOT NULL, cache_key TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', stale INTEGER NOT NULL DEFAULT 0,
 response_json TEXT, score REAL, confidence REAL, insufficient REAL,
 error TEXT NOT NULL DEFAULT '', cached INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(batch_id,entry_id)
);
CREATE INDEX IF NOT EXISTS ix_jev_results_batch ON jev_results(batch_id,status);
CREATE INDEX IF NOT EXISTS ix_jev_voucher_batch ON jev_results(batch_id,voucher_key,id);
CREATE INDEX IF NOT EXISTS ix_jev_cache ON jev_results(cache_key,status,stale);
CREATE TABLE IF NOT EXISTS jev_reviews (
 voucher_key TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''
REVIEW_STATES = ('待核', '需补材料', '已核查无异常', '继续关注')
STATUS_LABELS = {'preview': '待确认', 'pending': '未分析', 'running': '分析中',
                 'success': '已分析', 'failed': '分析失败', 'cancelled': '已取消',
                 'interrupted': '已中断', 'completed': '已完成', 'partial': '部分失败',
                 'auth_failed': '认证失败', 'stale': '已过期'}
BOUNDARY = '账簿记载及 AI 判断均为核查线索，不代表关联关系、违法事实或最终法律结论已获证实。'
SEND_FIELDS = {'entry_date': '日期', 'fiscal_year': '会计年度', 'period': '期间',
               'code': '科目编码', 'name': '科目名称', 'summary': '摘要',
               'counterparty': '往来单位', 'auxiliary': '辅助核算', 'currency': '币种',
               'original_amount': '原币金额', 'debit': '借方金额', 'credit': '贷方金额',
               'related_party': '用户维护关联方名单匹配状态'}
_ACTIVE = {}
_ACTIVE_LOCK = threading.Lock()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def send_fields(row, parties) -> dict:
    data = {field: row[field] for field in SEND_FIELDS if field not in ('debit', 'credit', 'related_party')}
    data['debit'] = format_money(row['debit_cents'])
    data['credit'] = format_money(row['credit_cents'])
    match = related_party_match(row, parties)
    data['related_party'] = {'matched': bool(match), 'name': match,
        'basis': '用户维护名单命中，关系未经本功能核实' if match else '名单未匹配，不等于不存在关联关系'}
    return data


def fingerprint(row, payload) -> str:
    # Include local provenance to invalidate results even when source IDs are reused.
    return digest({'input': payload, 'voucher': voucher_key(row),
                   'source': [row[k] for k in ('sha256', 'original_path', 'sheet', 'row_no', 'raw_json', 'maker')]})


def active_job(path):
    with _ACTIVE_LOCK:
        return _ACTIVE.get(str(Path(path).resolve()))


def recover_interrupted(store):
    if active_job(store.path):
        return
    with store.db:
        store.db.execute("UPDATE jev_results SET status='interrupted' WHERE status='running'")
        store.db.execute("UPDATE jev_batches SET status='interrupted' WHERE status='running'")


def prepare_batch(store, scope: dict) -> int:
    """Save a complete local snapshot; never invokes the network or credentials."""
    version, rules = load_rules(store)
    parties = store.related_parties()
    scope = {k: v for k, v in scope.items() if k in ('account', 'search', 'fiscal_year', 'period', 'include_descendants', 'source_ids')}
    hashes = []
    with store.db:
        batch_id = store.db.execute('INSERT INTO jev_batches(rule_id,model,scope_json,snapshot_hash) VALUES(?,?,?,?)',
            (version, rules['model'], encode(scope), '')).lastrowid
        for row in iter_entries(store, **scope):
            payload = send_fields(row, parties)
            fp = fingerprint(row, payload)
            hashes.append(fp)
            cache = ''
            store.db.execute('''INSERT INTO jev_results(batch_id,entry_id,voucher_key,local_json,input_json,fingerprint,cache_key)
                VALUES(?,?,?,?,?,?,?)''', (batch_id, row['id'], voucher_key(row), encode(row), encode(payload), fp, cache))
        if not hashes:
            raise ValueError('选定范围没有分录')
        for group in iter_voucher_groups(store, batch_id):
            context = [json.loads(row['input_json']) for row in group]
            group_hash = digest(['voucher-request-v1', context, version, rules, rules['model']])
            for index, row in enumerate(group):
                cache = digest([group_hash, index])
                store.db.execute('UPDATE jev_results SET cache_key=? WHERE id=?', (cache, row['id']))
        store.db.execute('UPDATE jev_batches SET snapshot_hash=? WHERE id=?', (digest(hashes), batch_id))
    return batch_id


def discard_preview(store, batch_id):
    with store.db:
        if store.db.execute("SELECT 1 FROM jev_batches WHERE id=? AND status='preview'", (batch_id,)).fetchone():
            store.db.execute('DELETE FROM jev_results WHERE batch_id=?', (batch_id,))
            store.db.execute('DELETE FROM jev_batches WHERE id=?', (batch_id,))


def preview_page(store, batch_id, page=0, page_size=20):
    return store.db.execute('SELECT id,input_json FROM jev_results WHERE batch_id=? ORDER BY id LIMIT ? OFFSET ?',
                            (batch_id, page_size, page * page_size)).fetchall()


def batch_info(store, batch_id):
    return store.db.execute('''SELECT b.*,r.body_json FROM jev_batches b JOIN jev_rules r ON r.id=b.rule_id
        WHERE b.id=?''', (batch_id,)).fetchone()


def batch_counts(store, batch_id) -> dict:
    counts = {row['status']: row['n'] for row in store.db.execute(
        'SELECT status,COUNT(*) n FROM jev_results WHERE batch_id=? GROUP BY status', (batch_id,))}
    counts['total'] = sum(counts.values())
    counts['uncertain'] = store.db.execute('''SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND status='success'
        AND stale=0 AND (confidence<0.5 OR insufficient>=0.5)''', (batch_id,)).fetchone()[0]
    counts['stale'] = store.db.execute('SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND stale=1', (batch_id,)).fetchone()[0]
    counts['vouchers'] = store.db.execute('SELECT COUNT(DISTINCT voucher_key) FROM jev_results WHERE batch_id=?', (batch_id,)).fetchone()[0]
    counts['cached'] = store.db.execute('SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND cached=1', (batch_id,)).fetchone()[0]
    return counts


def refresh_staleness(store, batch_id):
    parties = store.related_parties()
    current = {row['id']: fingerprint(row, send_fields(row, parties)) for row in iter_entries(store)}
    with store.db:
        for result in store.db.execute('SELECT id,entry_id,fingerprint,stale FROM jev_results WHERE batch_id=?', (batch_id,)).fetchall():
            stale = int(current.get(result['entry_id']) != result['fingerprint'])
            # Once invalidated, a prior result remains historical even after another re-import.
            if stale and not result['stale']:
                store.db.execute('UPDATE jev_results SET stale=1 WHERE id=?', (result['id'],))


def questions_for(rules):
    common = rules['common'] + '\n\n'
    questions = {topic['id']: Noul(instructions=common + topic['question']) for topic in rules['topics']}
    questions['priority'] = Score(instructions=common + rules['priority'], criteria=rules['levels'])
    questions['insufficient'] = Noul(instructions=common + rules['insufficient'])
    return questions


def voucher_questions(payloads, rules):
    questions = {}
    for index, _payload in enumerate(payloads):
        prefix = (f"本问题仅判断 `entries[{index}]` 这一条选定分录。"
                  "其他分录一并发送是为合并同一凭证的请求，不能把其他行独有的事项算作本行命中。\n\n")
        for key, question in questions_for(rules).items():
            instructions = prefix + question.instructions
            questions[f'entry_{index}_{key}'] = Score(instructions=instructions, criteria=rules['levels']) if key == 'priority' else Noul(instructions=instructions)
    return questions


def decode_voucher_response(raw, payloads, rules):
    items = []
    keys = [t['id'] for t in rules['topics']] + ['priority', 'insufficient']
    for index, _payload in enumerate(payloads):
        data = validate_response({'model': raw['model'], 'usage': raw['usage'],
            'answers': {key: raw['answers'][f'entry_{index}_{key}'] for key in keys}}, rules)
        data['shared_entries'] = len(payloads)
        data['request_group'] = digest(['voucher-request-v1', payloads, rules])
        items.append(data)
    return items


def validate_response(data, rules):
    if not isinstance(data, dict) or not isinstance(data.get('model'), str) or not data['model']:
        raise ValueError('响应缺少模型')
    if data['model'] != rules['model']:
        raise ValueError('响应模型与本批次固定模型不一致')
    def number(value, maximum):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('响应数值无效')
        return value
    answers = data['answers']
    for key in [*(topic['id'] for topic in rules['topics']), 'insufficient']:
        if answers[key]['type'] != 'noul':
            raise ValueError('响应类型无效')
        number(answers[key]['noul'], 1)
    priority = answers['priority']
    if priority['type'] != 'score':
        raise ValueError('响应类型无效')
    score = number(priority['score'], 4)
    confidence = number(priority['confidence'], 1)
    probs = priority['probabilities']
    if {str(k) for k in probs} != {'0', '1', '2', '3', '4'}:
        raise ValueError('响应缺少等级概率')
    values = {int(k): number(v, 1) for k, v in probs.items()}
    if abs(sum(values.values()) - 1) > 0.02 or abs(sum(k * v for k, v in values.items()) - score) > 0.03:
        raise ValueError('响应概率与评分不一致')
    usage = data['usage']
    if any(type(usage[k]) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        raise ValueError('响应用量无效')
    # Persist only documented structured fields, never arbitrary headers/error bodies.
    clean = {'model': data['model'], 'answers': {
        key: {'type': 'noul', 'noul': answers[key]['noul']}
        for key in [*(topic['id'] for topic in rules['topics']), 'insufficient']},
        'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens')}}
    clean['answers']['priority'] = {'type': 'score', 'score': score, 'confidence': confidence,
                                     'probabilities': probs, 'legend': dict(enumerate(rules['levels']))}
    return clean


def error_kind(exc):
    name = type(exc).__name__
    status = getattr(exc, 'status_code', None)
    if status in (401, 403) or name in ('TypeSafeAuthenticationError', 'TypeSafePermissionDeniedError'):
        return 'auth', '认证失败，请检查或替换 API Key'
    if status in (408, 429) or (isinstance(status, int) and 500 <= status < 600) or name in (
        'TypeSafeRateLimitError', 'TypeSafeAPIConnectionError', 'TypeSafeAPITimeoutError', 'TimeoutError', 'ConnectionError'):
        return 'retry', '网络、限流或服务暂时异常'
    if isinstance(exc, (ValueError, KeyError, TypeError)) or name == 'TypeSafeAPIResponseValidationError':
        return 'invalid', '返回字段缺失或格式无效'
    return 'error', '请求失败，请检查服务状态或规则配置'


def request_review(payload, rules, api_key, cancel, client_factory=TypeSafeClient, before_send=None):
    for attempt in range(1, 4):
        if cancel.is_set():
            return {'status': 'cancelled', 'attempts': attempt - 1}
        while not REQUEST_SLOTS.acquire(timeout=0.1):
            if cancel.is_set():
                return {'status': 'cancelled', 'attempts': attempt - 1}
        try:
            if cancel.is_set():
                return {'status': 'cancelled', 'attempts': attempt - 1}
            if before_send is not None and not before_send():
                return {'status': 'interrupted', 'stale': True, 'error': '来源或名单已变化，请重新预览', 'attempts': attempt - 1}
            with client_factory(api_key=api_key, model=rules['model'], timeout=25.0, retry=RetryPolicy(max_retries=0)) as client:
                grouped = isinstance(payload, list)
                response = client.system_one(state={'entries': payload} if grouped else payload,
                    questions=voucher_questions(payload, rules) if grouped else questions_for(rules))
            raw = response.model_dump(mode='json')
            if grouped:
                items = decode_voucher_response(raw, payload, rules)
                return {'status': 'success', 'items': items, 'attempts': attempt}
            data = validate_response(raw, rules)
            return {'status': 'success', 'data': data, 'attempts': attempt}
        except Exception as exc:
            kind, message = error_kind(exc)
            if kind == 'auth':
                cancel.set()
            if kind != 'retry' or attempt == 3:
                return {'status': 'failed', 'error': message, 'auth': kind == 'auth', 'attempts': attempt}
        finally:
            REQUEST_SLOTS.release()
        cancel.wait(min(2 ** (attempt - 1), 4))
    raise AssertionError('unreachable')


def test_connection(api_key, client_factory=TypeSafeClient):
    from .jev_rules import default_rules
    payload = {field: '' for field in SEND_FIELDS}
    payload.update(summary='虚构样例：支付办公文具采购款', name='管理费用', debit='10.00', credit='0.00',
                   related_party={'matched': False, 'name': '', 'basis': '虚构测试，不含账套信息'})
    return request_review(payload, default_rules(), api_key, threading.Event(), client_factory)


def snapshot_is_current(path, snapshot):
    # A separate connection belongs to this worker; validate again after waiting for a slot.
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("""SELECT e.*,a.code,a.name,s.sha256,s.original_path,s.sheet FROM entries e
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id WHERE e.id=?""", (snapshot['entry_id'],)).fetchone()
        stale = db.execute('SELECT stale FROM jev_results WHERE id=?', (snapshot['id'],)).fetchone()
        return bool(row and stale and not stale[0] and fingerprint(row, send_fields(row, db.execute('SELECT * FROM related_parties').fetchall())) == snapshot['fingerprint'])
    finally:
        db.close()


def iter_voucher_groups(store, batch_id):
    # Only keys are retained; each voucher's selected entries are loaded once.
    keys = store.db.execute('SELECT voucher_key FROM jev_results WHERE batch_id=? GROUP BY voucher_key ORDER BY MIN(id)', (batch_id,)).fetchall()
    for key in keys:
        yield store.db.execute('SELECT * FROM jev_results WHERE batch_id=? AND voucher_key=? ORDER BY id', (batch_id, key[0])).fetchall()


class ReviewJob:
    def __init__(self, path, batch_id, api_key, client_factory=TypeSafeClient):
        self.path = Path(path)
        self.batch_id = batch_id
        self._key = api_key
        self.client_factory = client_factory
        self.cancel = threading.Event()
        self.events = Queue()
        self.thread = None

    def start(self, store):
        """Called only by the explicit confirm action after previewing this batch."""
        path = str(self.path.resolve())
        if not self._key:
            raise ValueError('请先设置 Jev API Key')
        with _ACTIVE_LOCK:
            if path in _ACTIVE:
                raise ValueError('当前账套已有正在运行的审查批次')
            with store.db:
                info = batch_info(store, self.batch_id)
                if not info or info['status'] != 'preview':
                    raise ValueError('批次已确认或失效，请重新预览')
                # Lock the database while validating the scope and marking consent.
                store.db.execute('UPDATE jev_batches SET snapshot_hash=snapshot_hash WHERE id=?', (self.batch_id,))
                parties = store.related_parties()
                hashes = [fingerprint(row, send_fields(row, parties)) for row in iter_entries(store, **json.loads(info['scope_json']))]
                if digest(hashes) != info['snapshot_hash'] or store.db.execute('SELECT 1 FROM jev_results WHERE batch_id=? AND stale=1 LIMIT 1', (self.batch_id,)).fetchone():
                    raise ValueError('分录或关联方信息已变化，请重新选择范围并确认')
                current_rule, _ = load_rules(store)
                if current_rule != info['rule_id']:
                    raise ValueError('规则已变化，请重新预览并确认')
                store.db.execute("UPDATE jev_batches SET status='running',confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (self.batch_id,))
            _ACTIVE[path] = self
        self.thread = threading.Thread(target=self._run, daemon=True, name='jev-review')
        self.thread.start()

    def _save(self, store, row, result, cached=False):
        with store.db:
            if result['status'] == 'success':
                data = result['data']
                score = data['answers']['priority']
                store.db.execute('''UPDATE jev_results SET status='success',response_json=?,score=?,confidence=?,
                    insufficient=?,cached=?,attempts=?,error='',updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (encode(data), score['score'] / 4 * 100, score['confidence'], data['answers']['insufficient']['noul'],
                     int(cached), result.get('attempts', 0), row['id']))
            else:
                store.db.execute('UPDATE jev_results SET status=?,error=?,attempts=?,stale=MAX(stale,?),updated_at=CURRENT_TIMESTAMP WHERE id=?',
                    (result['status'], result.get('error', ''), result.get('attempts', 0), int(result.get('stale', False)), row['id']))

    def _run(self):
        from .store import LedgerStore
        store = None
        auth_failed = False
        try:
            store = LedgerStore(self.path)
            info = batch_info(store, self.batch_id)
            rules = json.loads(info['body_json'])
            # Queue at most four tasks, so cancellation never leaves a large request backlog.
            groups = iter_voucher_groups(store, self.batch_id)
            exhausted = False
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='jev-call') as pool:
                pending = {}
                while pending or not exhausted:
                    while not self.cancel.is_set() and not exhausted and len(pending) < 4:
                        group = next(groups, None)
                        if group is None:
                            exhausted = True
                            break
                        if not all(snapshot_is_current(self.path, row) for row in group):
                            for row in group:
                                self._save(store, row, {'status': 'interrupted', 'stale': True,
                                    'error': '本凭证的选定分录或名单已变化，请重新预览'})
                            continue
                        cached = [store.db.execute("SELECT response_json FROM jev_results WHERE cache_key=? AND status='success' AND stale=0 AND id<>? ORDER BY id DESC LIMIT 1", (row['cache_key'], row['id'])).fetchone() for row in group]
                        if all(cached):
                            for row, previous in zip(group, cached):
                                self._save(store, row, {'status': 'success', 'data': json.loads(previous['response_json'])}, cached=True)
                            continue
                        with store.db:
                            store.db.executemany("UPDATE jev_results SET status='running' WHERE id=?", [(row['id'],) for row in group])
                        payloads = [json.loads(row['input_json']) for row in group]
                        future = pool.submit(request_review, payloads, rules, self._key, self.cancel, self.client_factory,
                            lambda snapshots=group: all(snapshot_is_current(self.path, row) for row in snapshots))
                        pending[future] = group
                    if self.cancel.is_set():
                        exhausted = True
                    if pending:
                        done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            group = pending.pop(future)
                            result = future.result()
                            auth_failed |= result.get('auth', False)
                            for index, row in enumerate(group):
                                entry_result = {'status': 'success', 'data': result['items'][index], 'attempts': result['attempts']} if result['status'] == 'success' else result
                                self._save(store, row, entry_result)
                        if done:
                            self.events.put(('progress', batch_counts(store, self.batch_id)))
            with store.db:
                if self.cancel.is_set():
                    store.db.execute("UPDATE jev_results SET status='cancelled' WHERE batch_id=? AND status IN ('pending','running')", (self.batch_id,))
                counts = batch_counts(store, self.batch_id)
                status = 'auth_failed' if auth_failed else 'cancelled' if self.cancel.is_set() else 'completed' if counts.get('success', 0) == counts['total'] else 'partial'
                store.db.execute('UPDATE jev_batches SET status=?,finished_at=CURRENT_TIMESTAMP WHERE id=?', (status, self.batch_id))
            refresh_staleness(store, self.batch_id)
        except Exception:
            # Do not surface exception text: SDK errors may contain request headers or inputs.
            self.events.put(('error', '审查中断，已完成结果保留；可重新预览后恢复。'))
            if store:
                with store.db:
                    store.db.execute("UPDATE jev_results SET status='interrupted' WHERE batch_id=? AND status IN ('pending','running')", (self.batch_id,))
                    store.db.execute("UPDATE jev_batches SET status='interrupted' WHERE id=?", (self.batch_id,))
        finally:
            self._key = ''
            if store:
                store.close()
            with _ACTIVE_LOCK:
                _ACTIVE.pop(str(self.path.resolve()), None)
            self.events.put(('done', self.batch_id))


def save_review(store, key, status, note):
    if status not in REVIEW_STATES:
        raise ValueError('无效复核状态')
    with store.db:
        store.db.execute('''INSERT INTO jev_reviews(voucher_key,status,note) VALUES(?,?,?) ON CONFLICT(voucher_key)
            DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=CURRENT_TIMESTAMP''', (key, status, note))


def topic_labels(result, rules):
    if not result['response_json']:
        return []
    answers = json.loads(result['response_json'])['answers']
    return [(topic['id'], topic['label'] + ('（主题待确认）' if answers[topic['id']]['noul'] < 0.65 else ''))
            for topic in rules['topics'] if answers[topic['id']]['noul'] >= 0.35]


def voucher_results(store, batch_id, tier='谨慎', lane='scored', account='', topic='', period=''):
    info = batch_info(store, batch_id)
    if not info:
        return []
    rules = json.loads(info['body_json'])
    # Threshold edits are local display controls; historical question/advice versions stay intact.
    _, display_rules = load_rules(store)
    threshold = display_rules['thresholds'][tier]
    notes = {r['voucher_key']: dict(r) for r in store.db.execute('SELECT * FROM jev_reviews')}
    totals = {}
    for row in iter_entries(store):
        key = voucher_key(row)
        totals[key] = totals.get(key, 0) + 1
    groups = {}
    for raw in store.db.execute('SELECT * FROM jev_results WHERE batch_id=? ORDER BY id', (batch_id,)):
        row = dict(raw)
        row['local'] = json.loads(row['local_json'])
        row['themes'] = topic_labels(row, rules)
        groups.setdefault(row['voucher_key'], []).append(row)
    result = []
    for key, lines in groups.items():
        successful = [r for r in lines if r['status'] == 'success' and not r['stale']]
        max_score = max((r['score'] for r in successful), default=None)
        if lane == 'scored':
            hits = [r for r in successful if r['score'] >= threshold]
        elif lane == 'uncertain':
            hits = [r for r in successful if r['confidence'] < 0.5 or r['insufficient'] >= 0.5]
        elif lane == 'unfinished':
            hits = [r for r in lines if r['status'] != 'success' or r['stale']]
        else:
            hits = lines
        if not hits:
            continue
        if account:
            hits = [r for r in hits if account.casefold() in (r['local']['code'] + ' ' + r['local']['name']).casefold()]
        if topic:
            hits = [r for r in hits if topic in [t[0] for t in r['themes']] or (topic == 'unclassified' and not r['themes'])]
        if period:
            hits = [r for r in hits if period == f"{r['local']['fiscal_year']}/{r['local']['period']}"]
        if not hits:
            continue
        amounts = {}
        for row in hits:
            local = row['local']
            currency = local['currency'] or '币种未注明'
            amounts[currency] = max(amounts.get(currency, 0), abs(local['debit_cents']), abs(local['credit_cents']))
        currencies = tuple(sorted(amounts))
        result.append({'key': key, 'lines': lines, 'hits': hits, 'score': max_score,
            'themes': list(dict.fromkeys(label for r in hits for _, label in r['themes'])) or ['待分类'],
            'amounts': amounts, 'sort_currency': currencies,
            'sort_amount': amounts[currencies[0]] if len(currencies) == 1 else 0,
            'partial': len(successful) < max(totals.get(key, 0), len(lines)),
            'local_total': totals.get(key, 0), 'review': notes.get(key, {'status': '待核', 'note': ''})})
    # Group currency before amounts; no numeric comparison across different currencies.
    return sorted(result, key=lambda g: (-(g['score'] if g['score'] is not None else -1), g['sort_currency'], -g['sort_amount'], g['key']))
