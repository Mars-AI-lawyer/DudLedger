"""SQLite persistence and read-only ledger queries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .importer import ImportSpec, file_hash, format_money, normalized_rows


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, filename TEXT NOT NULL,
 original_path TEXT NOT NULL, sheet TEXT NOT NULL, sha256 TEXT NOT NULL,
 mapping_json TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 row_count INTEGER NOT NULL DEFAULT 0,
 UNIQUE(sha256, sheet, kind)
);
CREATE TABLE IF NOT EXISTS accounts (
 key TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
 row_no INTEGER NOT NULL, entry_date TEXT NOT NULL, voucher_no TEXT NOT NULL,
 voucher_type TEXT NOT NULL, account_key TEXT NOT NULL REFERENCES accounts(key),
 summary TEXT NOT NULL, counterparty TEXT NOT NULL, auxiliary TEXT NOT NULL,
 debit_cents INTEGER NOT NULL, credit_cents INTEGER NOT NULL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS balances (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
 row_no INTEGER NOT NULL, account_key TEXT NOT NULL REFERENCES accounts(key),
 opening_debit_cents INTEGER NOT NULL, opening_credit_cents INTEGER NOT NULL,
 period_debit_cents INTEGER NOT NULL, period_credit_cents INTEGER NOT NULL,
 closing_debit_cents INTEGER NOT NULL, closing_credit_cents INTEGER NOT NULL,
 raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_entries_account ON entries(account_key, entry_date, id);
CREATE INDEX IF NOT EXISTS ix_entries_voucher ON entries(entry_date, voucher_type, voucher_no);
CREATE INDEX IF NOT EXISTS ix_entries_search ON entries(counterparty, auxiliary);
CREATE INDEX IF NOT EXISTS ix_balances_account ON balances(account_key);
"""


def account_key(code: str, name: str) -> str:
    return "C:" + code if code else "N:" + name


class LedgerStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.exists() and self.path.stat().st_size > 0
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        try:
            if existing and not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_meta'").fetchone():
                raise ValueError("这不是本软件创建的账套文件")
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO ledger_meta(key,value) VALUES('format','legal-ledger-viewer-v1')")
            if self.db.execute("SELECT value FROM ledger_meta WHERE key='format'").fetchone()[0] != "legal-ledger-viewer-v1":
                raise ValueError("账套格式版本不兼容")
            self.db.commit()
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def import_sheet(self, spec: ImportSpec) -> int:
        spec.validate()
        digest = file_hash(spec.path)
        with self.db:
            cur = self.db.execute(
                "INSERT INTO sources(kind,filename,original_path,sheet,sha256,mapping_json) VALUES(?,?,?,?,?,?)",
                (spec.kind, spec.path.name, str(spec.path.resolve()), spec.sheet, digest, json.dumps({"header_row": spec.header_row, "mapping": spec.mapping}, ensure_ascii=False)),
            )
            source_id = cur.lastrowid
            count = 0
            for row_no, data in normalized_rows(spec):
                code, name = data["account_code"], data["account_name"]
                key = account_key(code, name)
                self.db.execute("INSERT INTO accounts(key,code,name) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE accounts.name END", (key, code, name))
                if spec.kind == "entry":
                    self.db.execute(
                        "INSERT INTO entries(source_id,row_no,entry_date,voucher_no,voucher_type,account_key,summary,counterparty,auxiliary,debit_cents,credit_cents,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, row_no, data["date"], data["voucher_no"], data["voucher_type"], key, data["summary"], data["counterparty"], data["auxiliary"], data["debit_cents"], data["credit_cents"], data["raw_json"]),
                    )
                else:
                    self.db.execute(
                        "INSERT INTO balances(source_id,row_no,account_key,opening_debit_cents,opening_credit_cents,period_debit_cents,period_credit_cents,closing_debit_cents,closing_credit_cents,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (source_id, row_no, key, *(data[field + "_cents"] for field in ("opening_debit", "opening_credit", "period_debit", "period_credit", "closing_debit", "closing_credit")), data["raw_json"]),
                    )
                count += 1
            if count == 0:
                raise ValueError("没有识别到有效数据行，请检查表头和列映射")
            self.db.execute("UPDATE sources SET row_count=? WHERE id=?", (count, source_id))
        return count

    def sources(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM sources ORDER BY id").fetchall()

    def summary(self, search: str = "") -> list[sqlite3.Row]:
        pattern = f"%{search}%"
        return self.db.execute("""
          SELECT a.key,a.code,a.name,
          COALESCE(b.od,0) opening_debit,COALESCE(b.oc,0) opening_credit,
          COALESCE(b.pd,0) reported_debit,COALESCE(b.pc,0) reported_credit,
          COALESCE(b.cd,0) closing_debit,COALESCE(b.cc,0) closing_credit,
          COALESCE(e.debit,0) detail_debit,COALESCE(e.credit,0) detail_credit,
          COALESCE(e.n,0) detail_count, COALESCE(b.n,0) balance_count
          FROM accounts a
          LEFT JOIN (SELECT account_key,SUM(opening_debit_cents) od,SUM(opening_credit_cents) oc,
            SUM(period_debit_cents) pd,SUM(period_credit_cents) pc,
            SUM(closing_debit_cents) cd,SUM(closing_credit_cents) cc,COUNT(*) n
            FROM balances GROUP BY account_key) b ON b.account_key=a.key
          LEFT JOIN (SELECT account_key,SUM(debit_cents) debit,SUM(credit_cents) credit,COUNT(*) n
            FROM entries GROUP BY account_key) e ON e.account_key=a.key
          WHERE a.code LIKE ? OR a.name LIKE ? ORDER BY a.code,a.name
        """, (pattern, pattern)).fetchall()

    def entries(self, account: str | None = None, search: str = "", limit: int = 3000) -> list[sqlite3.Row]:
        where = []
        args: list[object] = []
        if account:
            where.append("e.account_key=?")
            args.append(account)
        if search:
            where.append("(a.code LIKE ? OR a.name LIKE ? OR e.summary LIKE ? OR e.voucher_no LIKE ? OR e.counterparty LIKE ? OR e.auxiliary LIKE ?)")
            args.extend([f"%{search}%"] * 6)
        clause = " WHERE " + " AND ".join(where) if where else ""
        return self.db.execute("""
          SELECT e.*,a.code,a.name,s.filename,s.sheet FROM entries e
          JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
        """ + clause + " ORDER BY e.entry_date,e.id LIMIT ?", (*args, limit)).fetchall()

    def entry(self, entry_id: int) -> sqlite3.Row | None:
        return self.db.execute("""SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path
            FROM entries e JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
            WHERE e.id=?""", (entry_id,)).fetchone()

    def voucher_lines(self, entry_id: int) -> list[sqlite3.Row]:
        target = self.entry(entry_id)
        if not target:
            return []
        if not target["voucher_no"]:
            return [target]
        return self.db.execute("""SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path
            FROM entries e JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
            WHERE e.entry_date=? AND e.voucher_type=? AND e.voucher_no=?
            ORDER BY e.source_id,e.row_no""", (target["entry_date"], target["voucher_type"], target["voucher_no"])).fetchall()

    def auxiliary_totals(self, account: str | None = None) -> list[sqlite3.Row]:
        return self.db.execute("""SELECT COALESCE(NULLIF(e.auxiliary,''),NULLIF(e.counterparty,''),'未注明') label,
          SUM(e.debit_cents) debit,SUM(e.credit_cents) credit,COUNT(*) n
          FROM entries e WHERE (? IS NULL OR e.account_key=?)
          GROUP BY label ORDER BY n DESC,label LIMIT 1000""", (account, account)).fetchall()

    def export_entries(self, path: Path, account: str | None = None) -> int:
        import csv
        query = """SELECT e.*,a.code,a.name,s.filename,s.sheet FROM entries e
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id"""
        args = () if account is None else (account,)
        if account is not None:
            query += " WHERE e.account_key=?"
        query += " ORDER BY e.entry_date,e.id"
        cursor = self.db.execute(query, args)
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["日期", "凭证字", "凭证号", "科目编码", "科目名称", "摘要", "往来单位", "辅助核算", "借方金额", "贷方金额", "源文件", "工作表", "原行号"])
            for row in cursor:
                writer.writerow([row["entry_date"], row["voucher_type"], row["voucher_no"], row["code"], row["name"], row["summary"], row["counterparty"], row["auxiliary"], format_money(row["debit_cents"]), format_money(row["credit_cents"]), row["filename"], row["sheet"], row["row_no"]])
                count += 1
        return count
