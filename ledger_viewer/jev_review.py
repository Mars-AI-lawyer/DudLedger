"""Consent-bound snapshots, bounded Jev jobs, and evidence-linked review results."""
from __future__ import annotations

import hashlib
import json
import math
import copy
import re
import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from queue import Queue

from typesafe_sdk import Noul, TypeSafeClient, RetryPolicy

from .analysis_api import iter_entries
from .importer import format_money
from .jev_rules import load_rules
from .related_parties import party_names
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
                 'auth_failed': '认证失败', 'stale': '已过期', 'skipped': '已跳过结转损益'}
BOUNDARY = '账簿记载及 AI 判断均为核查线索，不代表关联关系、违法事实或最终法律结论已获证实。'
SEND_FIELDS = {'entry_date': '日期', 'name': '科目', 'summary': '摘要', 'counterparty': '往来单位',
               'auxiliary': '辅助核算', 'debit': '借方金额', 'credit': '贷方金额'}
REQUEST_VERSION = 'compact-voucher-v3'
# UTF-8 bytes are a conservative proxy, not an exact tokenizer count.
MAX_STATE_BYTES = 6000
MAX_PARTY_BYTES = 16000
MAX_REQUEST_BYTES = 64000
MAX_CONTEXT_BYTES = 40000
_ACTIVE = {}
_ACTIVE_LOCK = threading.Lock()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def send_fields(row, parties=None) -> dict:
    """Only send useful per-row fields: blanks and the zero-amount side stay local."""
    payload = {'entry_date': row['entry_date'], 'name': row['name'], 'summary': row['summary']}
    counterparty = (row['counterparty'] or '').strip()
    auxiliary = (row['auxiliary'] or '').strip()
    if counterparty:
        payload['counterparty'] = counterparty
    if auxiliary:
        payload['auxiliary'] = auxiliary
    if row['debit_cents']:
        payload['debit'] = format_money(row['debit_cents'])
    if row['credit_cents']:
        payload['credit'] = format_money(row['credit_cents'])
    return payload


def _sum_amounts(values: list[str]) -> str:
    # Amounts are plain two-decimal strings; summing happens in code, never in the model.
    total = sum((Decimal(value) for value in values), Decimal(0))
    cents = int((total * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return format_money(cents)


def merge_duplicate_rows(payloads):
    """Merge rows identical apart from amounts: total each side locally and mark repeat counts."""
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for payload in payloads:
        base = {key: value for key, value in payload.items() if key not in ('debit', 'credit')}
        key = (encode(base), 'debit' in payload, 'credit' in payload)
        if key not in groups:
            groups[key] = {'base': base, 'debit': [], 'credit': []}
            order.append(key)
        for side in ('debit', 'credit'):
            if side in payload:
                groups[key][side].append(payload[side])
    merged = []
    for key in order:
        group = groups[key]
        row = dict(group['base'])
        for side in ('debit', 'credit'):
            if group[side]:
                row[side] = _sum_amounts(group[side])
        count = len(group['debit'] or group['credit'])
        if count > 1:
            row['repeat'] = count
        merged.append(row)
    return merged


def closing_voucher(rows) -> bool:
    """Only skip an entire unambiguous profit/loss closing voucher."""
    names = [re.sub(r'\s+', '', r['name']) for r in rows]
    if '本年利润' not in names or not any(n != '本年利润' for n in names):
        return False
    if sum(r['debit_cents'] for r in rows) != sum(r['credit_cents'] for r in rows):
        return False
    if not all(n == '本年利润' or n.startswith(('主营业务收入', '其他业务收入', '营业外收入',
            '主营业务成本', '其他业务成本', '营业外支出', '管理费用', '销售费用', '财务费用',
            '研发费用', '税金及附加', '营业税金及附加', '所得税费用', '投资收益', '公允价值变动损益',
            '资产减值损失', '信用减值损失', '资产处置收益', '其他收益')) for n in names):
        return False
    pattern = r'(?:[0-9年月期末本当度\-/.]*)(?:自动)?(?:(?:结转|结平)(?:本期|本月|本年|年度|当期|期末|年末)?(?:损益|利润|收入|成本|费用|成本费用|本年利润)|(?:损益|利润)结转)[。；; ]*'
    return all(re.fullmatch(pattern, re.sub(r'\s+', '', r['summary'])) for r in rows)


def compact_state(payloads, parties=None):
    """Merge repeated rows, hoist shared fields, and attach the related-party name list."""
    merged = merge_duplicate_rows(payloads)
    common = {}
    for key in merged[0] if merged else ():
        if all(row.get(key) == merged[0].get(key) for row in merged):
            common[key] = merged[0][key]
    entries = [{k: v for k, v in row.items() if k not in common} for row in merged]
    state = {}
    if parties:
        state['related_parties'] = parties
    state.update({'common': common, 'entries': entries})
    return state


def request_parts(payloads):
    pieces = []
    for payload in payloads:
        summary = payload['summary']
        if len(summary.encode('utf-8')) > 2400:
            # Overlap preserves words/negations around a split; never drop a tail.
            for start in range(0, len(summary), 520):
                pieces.append({**payload, 'summary': summary[start:start + 600]})
        else:
            pieces.append(payload)
    chunk = []
    for piece in pieces:
        if len(encode(compact_state([piece])).encode('utf-8')) > MAX_STATE_BYTES:
            raise ValueError('科目或日期字段过长，请核对导入列映射后重试；未截断分析')
        if chunk and len(encode(compact_state(chunk + [piece])).encode('utf-8')) > MAX_STATE_BYTES:
            yield chunk
            chunk = []
        chunk.append(piece)
    if chunk:
        yield chunk


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
    parties = party_names(store.related_parties())
    scope = {k: v for k, v in scope.items() if k in ('account', 'search', 'fiscal_year', 'period', 'include_descendants', 'source_ids')}
    hashes = []
    with store.db:
        batch_id = store.db.execute('INSERT INTO jev_batches(rule_id,model,scope_json,snapshot_hash) VALUES(?,?,?,?)',
            (version, rules['model'], encode(scope), '')).lastrowid
        for row in iter_entries(store, **scope):
            payload = send_fields(row)
            fp = fingerprint(row, payload)
            hashes.append(fp)
            cache = ''
            store.db.execute('''INSERT INTO jev_results(batch_id,entry_id,voucher_key,local_json,input_json,fingerprint,cache_key)
                VALUES(?,?,?,?,?,?,?)''', (batch_id, row['id'], voucher_key(row), encode(row), encode(payload), fp, cache))
        if not hashes:
            raise ValueError('选定范围没有分录')
        all_groups = {}
        for row in iter_entries(store):
            all_groups.setdefault(voucher_key(row), []).append(row)
        for group in iter_voucher_groups(store, batch_id):
            if closing_voucher(all_groups[group[0]['voucher_key']]):
                store.db.executemany("UPDATE jev_results SET status='skipped',error='结转损益凭证，未发送 Jev' WHERE id=?",
                                     [(r['id'],) for r in group])
            context = [json.loads(row['input_json']) for row in group]
            group_hash = digest([REQUEST_VERSION, context, parties, version, rules, rules['model']])
            for index, row in enumerate(group):
                cache = digest([group_hash, index])
                store.db.execute('UPDATE jev_results SET cache_key=? WHERE id=?', (cache, row['id']))
        store.db.execute('UPDATE jev_batches SET snapshot_hash=? WHERE id=?',
            (digest({'entries': hashes, 'parties': parties}), batch_id))
    return batch_id


def discard_preview(store, batch_id):
    with store.db:
        if store.db.execute("SELECT 1 FROM jev_batches WHERE id=? AND status='preview'", (batch_id,)).fetchone():
            store.db.execute('DELETE FROM jev_results WHERE batch_id=?', (batch_id,))
            store.db.execute('DELETE FROM jev_batches WHERE id=?', (batch_id,))


def preview_page(store, batch_id, page=0, page_size=20):
    return store.db.execute('SELECT id,input_json,status FROM jev_results WHERE batch_id=? ORDER BY id LIMIT ? OFFSET ?',
                            (batch_id, page_size, page * page_size)).fetchall()


def batch_info(store, batch_id):
    return store.db.execute('''SELECT b.*,r.body_json FROM jev_batches b JOIN jev_rules r ON r.id=b.rule_id
        WHERE b.id=?''', (batch_id,)).fetchone()


def batch_counts(store, batch_id) -> dict:
    counts = {row['status']: row['n'] for row in store.db.execute(
        'SELECT status,COUNT(*) n FROM jev_results WHERE batch_id=? GROUP BY status', (batch_id,))}
    counts['total'] = sum(counts.values())
    counts['uncertain'] = store.db.execute('''SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND status='success'
        AND stale=0 AND (confidence<0.5 OR insufficient>=0.5 OR json_extract(response_json,'$.needs_manual_review')=1)''', (batch_id,)).fetchone()[0]
    counts['stale'] = store.db.execute('SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND stale=1', (batch_id,)).fetchone()[0]
    counts['vouchers'] = store.db.execute('SELECT COUNT(DISTINCT voucher_key) FROM jev_results WHERE batch_id=?', (batch_id,)).fetchone()[0]
    groups = store.db.execute('''SELECT
        MIN(status IN ('success','failed','skipped')) finished,
        MIN(status='success') successful,
        MAX(status='failed') failed
        FROM jev_results WHERE batch_id=? GROUP BY voucher_key''', (batch_id,)).fetchall()
    counts['finished_vouchers'] = sum(row['finished'] for row in groups)
    counts['successful_vouchers'] = sum(row['successful'] for row in groups)
    counts['failed_vouchers'] = sum(row['finished'] and row['failed'] for row in groups)
    counts['skipped_vouchers'] = store.db.execute("SELECT COUNT(DISTINCT voucher_key) FROM jev_results WHERE batch_id=? AND status='skipped'", (batch_id,)).fetchone()[0]
    counts['cached'] = store.db.execute('SELECT COUNT(*) FROM jev_results WHERE batch_id=? AND cached=1', (batch_id,)).fetchone()[0]
    return counts


def refresh_staleness(store, batch_id):
    current = {row['id']: fingerprint(row, send_fields(row)) for row in iter_entries(store)}
    with store.db:
        for result in store.db.execute('SELECT id,entry_id,fingerprint,stale FROM jev_results WHERE batch_id=?', (batch_id,)).fetchall():
            stale = int(current.get(result['entry_id']) != result['fingerprint'])
            # Once invalidated, a prior result remains historical even after another re-import.
            if stale and not result['stale']:
                store.db.execute('UPDATE jev_results SET stale=1 WHERE id=?', (result['id'],))


def questions_for(rules):
    common = rules['common'] + '\n\n'
    questions = {topic['id']: Noul(instructions=common + topic['question']) for topic in rules['topics']}
    questions['insufficient'] = Noul(instructions=common + rules['insufficient'])
    return questions


def voucher_questions(payloads, rules):
    adapted = copy.deepcopy(rules)
    adapted['common'] = ('判断当前凭证片段整体是否存在该事项，而非逐条分录评分。'
        '`common` 是各行共用字段，与 `entries` 各行合并理解；内容相同仅金额不同的行已合并为一行，'
        '金额为合并后合计、`repeat` 为行数，不能据此还原每行原始金额。'
        '片段可能不是完整凭证，不能据此判断借贷不平。\n' + adapted['common'])
    return questions_for(adapted)


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
    keys = [*(topic['id'] for topic in rules['topics']), 'insufficient']
    for key in keys:
        if answers[key]['type'] != 'noul':
            raise ValueError('响应类型无效')
        number(answers[key]['noul'], 1)
    usage = data['usage']
    if any(type(usage[k]) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        raise ValueError('响应用量无效')
    # Persist only documented structured fields, never arbitrary headers/error bodies.
    clean = {'model': data['model'], 'answers': {
        key: {'type': 'noul', 'noul': answers[key]['noul']} for key in keys},
        'usage': {k: usage[k] for k in ('input_tokens', 'output_tokens')}}
    return clean


def error_kind(exc):
    name = type(exc).__name__
    status = getattr(exc, 'status_code', None)
    if status in (401, 403) or name in ('TypeSafeAuthenticationError', 'TypeSafePermissionDeniedError'):
        return 'auth', '认证失败，请检查或替换 API Key'
    if status == 413 or (status in (400, 422) and any(word in str(exc).lower() for word in ('context length', 'context_length', 'too many tokens', 'token limit', 'context window', 'maximum context'))):
        return 'length', '请求内容超出模型限制'
    if status in (408, 429) or (isinstance(status, int) and 500 <= status < 600) or name in (
        'TypeSafeRateLimitError', 'TypeSafeAPIConnectionError', 'TypeSafeAPITimeoutError', 'TimeoutError', 'ConnectionError'):
        return 'retry', '网络、限流或服务暂时异常'
    if isinstance(exc, (ValueError, KeyError, TypeError)) or name == 'TypeSafeAPIResponseValidationError':
        return 'invalid', '返回字段缺失或格式无效'
    return 'error', '请求失败，请检查服务状态或规则配置'


def _request_once(payload, rules, api_key, cancel, client_factory=TypeSafeClient, before_send=None, parties=None):
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
                return {'status': 'interrupted', 'stale': True, 'error': '来源已变化，请重新预览', 'attempts': attempt - 1}
            with client_factory(api_key=api_key, model=rules['model'], timeout=25.0, retry=RetryPolicy(max_retries=0)) as client:
                grouped = isinstance(payload, list)
                response = client.system_one(state=compact_state(payload, parties) if grouped else payload,
                    questions=voucher_questions(payload, rules) if grouped else questions_for(rules))
            raw = response.model_dump(mode='json')
            if grouped:
                data = validate_response(raw, rules)
                return {'status': 'success', 'data': data, 'attempts': attempt}
            data = validate_response(raw, rules)
            return {'status': 'success', 'data': data, 'attempts': attempt}
        except Exception as exc:
            kind, message = error_kind(exc)
            if kind == 'auth':
                cancel.set()
            if kind != 'retry' or attempt == 3:
                return {'status': 'failed', 'error': message, 'auth': kind == 'auth', 'length': kind == 'length', 'attempts': attempt}
        finally:
            REQUEST_SLOTS.release()
        cancel.wait(min(2 ** (attempt - 1), 4))
    raise AssertionError('unreachable')


def request_review(payload, rules, api_key, cancel, client_factory=TypeSafeClient, before_send=None, parties=None):
    if not isinstance(payload, list):
        return _request_once(payload, rules, api_key, cancel, client_factory, before_send)
    questions = voucher_questions(payload, rules)
    serialized = {k: q.model_dump(mode='json') for k, q in questions.items()}
    question_bytes = len(encode(serialized).encode('utf-8'))
    longest = max(len(encode(q).encode('utf-8')) for q in serialized.values())
    party_bytes = len(encode(parties or []).encode('utf-8'))
    if party_bytes > MAX_PARTY_BYTES:
        return {'status': 'failed', 'error': '关联方名单过长，请精简名单或别名后重试', 'attempts': 0}
    if (question_bytes + MAX_STATE_BYTES + party_bytes > MAX_REQUEST_BYTES
            or longest + MAX_STATE_BYTES + party_bytes > MAX_CONTEXT_BYTES):
        return {'status': 'failed', 'error': '审查规则或关联方名单过长，请缩短后重试', 'attempts': 0}
    try:
        queue = list(request_parts(payload))
    except ValueError as exc:
        return {'status': 'failed', 'error': str(exc), 'attempts': 0}
    results, attempts = [], 0
    while queue:
        part = queue.pop(0)
        result = _request_once(part, rules, api_key, cancel, client_factory, before_send, parties)
        attempts += result['attempts']
        if result.get('length'):
            if len(part) > 1:
                middle = len(part) // 2
                queue[:0] = [part[:middle], part[middle:]]
                continue
            summary = part[0]['summary']
            if len(summary) > 160:
                middle = len(summary) // 2
                queue[:0] = [[{**part[0], 'summary': summary[:middle + 30]}],
                             [{**part[0], 'summary': summary[middle - 30:]}]]
                continue
            result['error'] = '精简后仍超过服务限制，请缩短规则或检查导入字段；未认定为低风险'
        if result['status'] != 'success':
            return {**result, 'attempts': attempts}
        results.append(result['data'])
    if not results:
        return {'status': 'failed', 'error': '没有可核查内容', 'attempts': 0}
    # Keep each topic's maximum across chunks; never average away a hit.
    merged = copy.deepcopy(results[0])
    for key in [t['id'] for t in rules['topics']] + ['insufficient']:
        merged['answers'][key]['noul'] = max(r['answers'][key]['noul'] for r in results)
    merged['usage'] = {k: sum(r['usage'][k] for r in results) for k in ('input_tokens', 'output_tokens')}
    merged.update(review_unit='voucher', shared_entries=len(payload), chunks=len(results),
                  needs_manual_review=len(results) > 1,
                  request_group=digest([REQUEST_VERSION, payload, rules, parties]))
    if len(results) > 1:
        merged['part_results'] = results
    return {'status': 'success', 'items': [merged for _ in payload], 'attempts': attempts}


def test_connection(api_key, client_factory=TypeSafeClient):
    from .jev_rules import default_rules
    payload = {'entry_date': '2026-01-05', 'name': '管理费用', 'summary': '虚构样例：支付办公文具采购款', 'debit': '10.00'}
    return request_review(payload, default_rules(), api_key, threading.Event(), client_factory)


def snapshot_is_current(path, snapshot):
    # A separate connection belongs to this worker; validate again after waiting for a slot.
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("""SELECT e.*,a.code,a.name,s.sha256,s.original_path,s.sheet FROM entries e
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id WHERE e.id=?""", (snapshot['entry_id'],)).fetchone()
        stale = db.execute('SELECT stale FROM jev_results WHERE id=?', (snapshot['id'],)).fetchone()
        return bool(row and stale and not stale[0] and fingerprint(row, send_fields(row)) == snapshot['fingerprint'])
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
                hashes = [fingerprint(row, send_fields(row)) for row in iter_entries(store, **json.loads(info['scope_json']))]
                snapshot = digest({'entries': hashes, 'parties': party_names(store.related_parties())})
                if snapshot != info['snapshot_hash'] or store.db.execute('SELECT 1 FROM jev_results WHERE batch_id=? AND stale=1 LIMIT 1', (self.batch_id,)).fetchone():
                    raise ValueError('分录或关联方名单已变化，请重新选择范围并确认')
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
                # A voucher's headline probability is its strongest topic hit, not an average.
                score = max(value['noul'] for key, value in data['answers'].items() if key != 'insufficient') * 100
                store.db.execute('''UPDATE jev_results SET status='success',response_json=?,score=?,confidence=NULL,
                    insufficient=?,cached=?,attempts=?,error='',updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                    (encode(data), score, data['answers']['insufficient']['noul'],
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
            # Freeze the party list for the whole run so cache keys and sent content match.
            parties = party_names(store.related_parties())
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
                        if all(row['status'] == 'skipped' for row in group):
                            self.events.put(('progress', None))
                            continue
                        if not all(snapshot_is_current(self.path, row) for row in group):
                            for row in group:
                                self._save(store, row, {'status': 'interrupted', 'stale': True,
                                    'error': '本凭证的选定分录已变化，请重新预览'})
                            self.events.put(('progress', None))
                            continue
                        cached = [store.db.execute("SELECT response_json FROM jev_results WHERE cache_key=? AND status='success' AND stale=0 AND id<>? ORDER BY id DESC LIMIT 1", (row['cache_key'], row['id'])).fetchone() for row in group]
                        if all(cached):
                            for row, previous in zip(group, cached):
                                self._save(store, row, {'status': 'success', 'data': json.loads(previous['response_json'])}, cached=True)
                            self.events.put(('progress', None))
                            continue
                        with store.db:
                            store.db.executemany("UPDATE jev_results SET status='running' WHERE id=?", [(row['id'],) for row in group])
                        payloads = [json.loads(row['input_json']) for row in group]
                        future = pool.submit(request_review, payloads, rules, self._key, self.cancel, self.client_factory,
                            lambda snapshots=group: all(snapshot_is_current(self.path, row) for row in snapshots), parties)
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
                            self.events.put(('progress', None))
            with store.db:
                if self.cancel.is_set():
                    store.db.execute("UPDATE jev_results SET status='cancelled' WHERE batch_id=? AND status IN ('pending','running')", (self.batch_id,))
                counts = batch_counts(store, self.batch_id)
                status = 'auth_failed' if auth_failed else 'cancelled' if self.cancel.is_set() else 'completed' if counts.get('success', 0) + counts.get('skipped', 0) == counts['total'] else 'partial'
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


def topic_probabilities(row) -> dict:
    if not row['response_json']:
        return {}
    answers = json.loads(row['response_json'])['answers']
    return {key: float(value['noul']) for key, value in answers.items()
            if key != 'insufficient' and isinstance(value, dict) and 'noul' in value}


def has_topic_hit(row, threshold) -> bool:
    """Any single topic reaching the tier threshold makes the voucher a problem voucher."""
    return any(probability * 100 >= threshold for probability in topic_probabilities(row).values())


def is_uncertain(row):
    data = json.loads(row['response_json']) if row['response_json'] else {}
    legacy_confidence = row['confidence'] is not None and row['confidence'] < 0.5
    return legacy_confidence or (row['insufficient'] or 0) >= 0.5 or data.get('needs_manual_review', False)


def needs_review(row, threshold, tier=None):
    return has_topic_hit(row, threshold) or is_uncertain(row)


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
        if lane == 'completed':
            hits = lines if all(r['status'] in ('success', 'failed', 'skipped') for r in lines) else []
        elif lane == 'candidates':
            hits = [r for r in successful if needs_review(r, threshold, tier)]
            if any(r['status'] not in ('success', 'failed', 'skipped') for r in lines):
                hits = []
            elif any(r['status'] == 'failed' for r in lines):
                hits = lines
        elif lane == 'scored':
            hits = [r for r in successful if has_topic_hit(r, threshold)]
        elif lane == 'uncertain':
            hits = [r for r in successful if is_uncertain(r)]
        elif lane == 'unfinished':
            hits = [r for r in lines if r['status'] not in ('success', 'skipped') or r['stale']]
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
            'partial': len(successful) + sum(r['status'] == 'skipped' for r in lines) < max(totals.get(key, 0), len(lines)),
            'local_total': totals.get(key, 0), 'review': notes.get(key, {'status': '待核', 'note': ''})})
    # Group currency before amounts; no numeric comparison across different currencies.
    return sorted(result, key=lambda g: (-(g['score'] if g['score'] is not None else -1), g['sort_currency'], -g['sort_amount'], g['key']))
