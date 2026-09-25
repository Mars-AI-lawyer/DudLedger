"""Read-only evidence interface reserved for future local AI analysis."""

from __future__ import annotations

from .store import LedgerStore


def candidate_entries(store: LedgerStore, keywords: list[str], limit: int = 5000) -> list[dict[str, object]]:
    """Return source-linked candidates; keyword hits are leads, not legal conclusions."""
    if not keywords:
        return []
    clauses = []
    args: list[object] = []
    for word in keywords:
        clauses.append("(a.name LIKE ? OR e.summary LIKE ? OR e.counterparty LIKE ? OR e.auxiliary LIKE ?)")
        args.extend([f"%{word}%"] * 4)
    query = """SELECT e.id,e.entry_date,e.fiscal_year,e.period,e.voucher_type,e.voucher_no,a.code,a.name,
        e.summary,e.counterparty,e.auxiliary,e.currency,e.original_amount,e.maker,e.debit_cents,e.credit_cents,
        s.filename,s.sheet,e.row_no,s.sha256 FROM entries e
        JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
        WHERE """ + " OR ".join(clauses) + " ORDER BY e.entry_date,e.id LIMIT ?"
    args.append(limit)
    return [dict(row) for row in store.db.execute(query, args)]


def iter_entries(store: LedgerStore, *, account=None, search='', fiscal_year=None,
                 period=None, include_descendants=False, source_ids=None, page_size=500):
    """Keyset pagination without a candidate limit; scope matches the detail view."""
    if not 1 <= page_size <= 5000:
        raise ValueError('分页大小须为 1 至 5000')
    where, args = [], []
    clause, values = store._account_clause(account, include_descendants)
    if clause:
        where.append(clause)
        args.extend(values)
    for field, value in (('fiscal_year', fiscal_year), ('period', period)):
        if value is not None:
            where.append(f'e.{field}=?')
            args.append(value)
    if search:
        where.append('(a.code LIKE ? OR a.name LIKE ? OR e.summary LIKE ? OR e.voucher_no LIKE ? OR e.counterparty LIKE ? OR e.auxiliary LIKE ?)')
        args.extend([f'%{search}%'] * 6)
    if source_ids is not None:
        if not source_ids:
            return
        where.append('e.source_id IN (' + ','.join('?' for _ in source_ids) + ')')
        args.extend(source_ids)
    last_id = 0
    base = '''SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path,s.sha256
        FROM entries e JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id WHERE '''
    while True:
        page = store.db.execute(base + ' AND '.join([*where, 'e.id>?']) + ' ORDER BY e.id LIMIT ?', (*args, last_id, page_size)).fetchall()
        if not page:
            return
        yield from (dict(row) for row in page)
        last_id = page[-1]['id']
