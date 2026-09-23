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
    query = """SELECT e.id,e.entry_date,e.voucher_type,e.voucher_no,a.code,a.name,
        e.summary,e.counterparty,e.auxiliary,e.debit_cents,e.credit_cents,
        s.filename,s.sheet,e.row_no,s.sha256 FROM entries e
        JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
        WHERE """ + " OR ".join(clauses) + " ORDER BY e.entry_date,e.id LIMIT ?"
    args.append(limit)
    return [dict(row) for row in store.db.execute(query, args)]
