"""SQLite persistence and read-only ledger queries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .importer import (
    DISPOSITION_IMPORT,
    DISPOSITION_PENDING,
    EXCLUSION_LABELS,
    REPORT_KINDS,
    ImportSpec,
    classify_rows,
    file_hash,
    format_money,
    formula_cells,
    header_for_row,
    header_signature,
    text_value,
)


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
 row_no INTEGER NOT NULL, entry_date TEXT NOT NULL, fiscal_year TEXT NOT NULL DEFAULT '',
 period TEXT NOT NULL DEFAULT '', voucher_no TEXT NOT NULL,
 voucher_type TEXT NOT NULL, account_key TEXT NOT NULL REFERENCES accounts(key),
 summary TEXT NOT NULL, counterparty TEXT NOT NULL, auxiliary TEXT NOT NULL,
 currency TEXT NOT NULL DEFAULT '', original_amount TEXT NOT NULL DEFAULT '',
 maker TEXT NOT NULL DEFAULT '',
 debit_cents INTEGER NOT NULL, credit_cents INTEGER NOT NULL, raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS balances (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
 row_no INTEGER NOT NULL, account_key TEXT NOT NULL REFERENCES accounts(key),
 opening_debit_cents INTEGER NOT NULL, opening_credit_cents INTEGER NOT NULL,
 period_debit_cents INTEGER NOT NULL, period_credit_cents INTEGER NOT NULL,
 year_debit_cents INTEGER NOT NULL DEFAULT 0, year_credit_cents INTEGER NOT NULL DEFAULT 0,
 closing_debit_cents INTEGER NOT NULL, closing_credit_cents INTEGER NOT NULL,
 raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_rows (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
 row_no INTEGER NOT NULL, left_item TEXT NOT NULL,
 left_amount_1_cents INTEGER, left_amount_2_cents INTEGER, left_amount_3_cents INTEGER,
 right_item TEXT NOT NULL, right_amount_1_cents INTEGER, right_amount_2_cents INTEGER, right_amount_3_cents INTEGER,
 raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voucher_marks (
 voucher_key TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '',
 signature TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS related_parties (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
 aliases_json TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
 roles_json TEXT NOT NULL DEFAULT '[]', relation TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS import_templates (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, sheet_label TEXT NOT NULL DEFAULT '',
 header_signature TEXT NOT NULL, mapping_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 used_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(kind, header_signature)
);
CREATE TABLE IF NOT EXISTS import_prechecks (
 id INTEGER PRIMARY KEY, source_id INTEGER UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, sheet TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL,
 header_row INTEGER NOT NULL, mapping_json TEXT NOT NULL, verdict TEXT NOT NULL,
 stats_json TEXT NOT NULL, issues_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_entries_account ON entries(account_key, entry_date, id);
CREATE INDEX IF NOT EXISTS ix_entries_voucher ON entries(entry_date, voucher_type, voucher_no);
CREATE INDEX IF NOT EXISTS ix_entries_search ON entries(counterparty, auxiliary);
CREATE INDEX IF NOT EXISTS ix_balances_account ON balances(account_key);
CREATE INDEX IF NOT EXISTS ix_report_rows_source ON report_rows(source_id,row_no);
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
            from .jev_review import SCHEMA as REVIEW_SCHEMA
            self.db.executescript(SCHEMA + REVIEW_SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO ledger_meta(key,value) VALUES('format','legal-ledger-viewer-v6')")
            version = self.db.execute("SELECT value FROM ledger_meta WHERE key='format'").fetchone()[0]
            if version not in ("legal-ledger-viewer-v1", "legal-ledger-viewer-v2", "legal-ledger-viewer-v3", "legal-ledger-viewer-v4", "legal-ledger-viewer-v5", "legal-ledger-viewer-v6", "legal-ledger-viewer-v7"):
                raise ValueError("账套格式版本不兼容")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(entries)")}
            for name in ("fiscal_year", "period", "currency", "original_amount", "maker"):
                if name not in columns:
                    self.db.execute(f"ALTER TABLE entries ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            # 尽调主体名单升级：身份与关系列（v7 及更早账套就地补列）。
            party_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(related_parties)")}
            if "roles_json" not in party_columns:
                self.db.execute("ALTER TABLE related_parties ADD COLUMN roles_json TEXT NOT NULL DEFAULT '[]'")
            if "relation" not in party_columns:
                self.db.execute("ALTER TABLE related_parties ADD COLUMN relation TEXT NOT NULL DEFAULT ''")
            review_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jev_reviews)")}
            if "feedback" not in review_columns:
                self.db.execute("ALTER TABLE jev_reviews ADD COLUMN feedback TEXT NOT NULL DEFAULT ''")
            result_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jev_results)")}
            if "features_json" not in result_columns:
                self.db.execute("ALTER TABLE jev_results ADD COLUMN features_json TEXT")
            if version == "legal-ledger-viewer-v1":
                self.db.execute("UPDATE entries SET fiscal_year=substr(entry_date,1,4), period=CAST(CAST(substr(entry_date,6,2) AS INTEGER) AS TEXT) WHERE entry_date GLOB '????-??-??'")
            for table, additions in (("balances", ("year_debit_cents", "year_credit_cents")), ("report_rows", ("left_amount_3_cents", "right_amount_3_cents"))):
                present = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
                for name in additions:
                    if name not in present:
                        default = " NOT NULL DEFAULT 0" if table == "balances" else ""
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} INTEGER{default}")
            if version != "legal-ledger-viewer-v7":
                self.db.execute("UPDATE ledger_meta SET value='legal-ledger-viewer-v7' WHERE key='format'")
            self.db.execute("CREATE INDEX IF NOT EXISTS ix_entries_voucher_period ON entries(fiscal_year,period,voucher_type,voucher_no)")
            self.db.commit()
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def display_columns(self, view: str) -> list[str] | None:
        row = self.db.execute("SELECT value FROM ledger_meta WHERE key=?", (f"display_columns:{view}",)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, list) and all(isinstance(key, str) for key in value) else None

    def save_display_columns(self, view: str, columns: list[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO ledger_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"display_columns:{view}", json.dumps(columns, ensure_ascii=False)),
            )

    def setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM ledger_meta WHERE key=?", (f"setting:{key}",)).fetchone()
        return row[0] if row else default

    def save_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO ledger_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"setting:{key}", value),
            )

    def voucher_widths(self) -> dict[str, int]:
        try:
            value = json.loads(self.setting("voucher_widths", "{}"))
        except (TypeError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {key: width for key, width in value.items() if isinstance(key, str) and type(width) is int and 65 <= width <= 2000}

    def save_voucher_widths(self, widths: dict[str, int]) -> None:
        self.save_setting("voucher_widths", json.dumps(widths, ensure_ascii=False))

    def reset_voucher_widths(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM ledger_meta WHERE key='setting:voucher_widths'")

    def related_parties(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM related_parties ORDER BY name,id").fetchall()

    def save_related_party(self, name: str, aliases: list[str], note: str = "", party_id: int | None = None,
                           roles: list[str] | None = None, relation: str = "") -> int:
        from .related_parties import normalized_name, parse_roles

        name = name.strip()
        relation = (relation or "").strip()
        roles = parse_roles(roles or [])
        aliases = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip() and alias.strip() != name))
        if not name:
            raise ValueError("尽调主体名称不能为空")
        if len(name) > 200 or any(len(alias) > 200 for alias in aliases) or len(relation) > 200:
            raise ValueError("名称、别名或关系不能超过 200 字")
        requested = {normalized_name(item) for item in (name, *aliases)}
        if len(requested) != 1 + len(aliases):
            raise ValueError("名称和别名不能重复")
        for existing in self.related_parties():
            if existing["id"] == party_id:
                continue
            previous = [existing["name"], *json.loads(existing["aliases_json"])]
            if requested & {normalized_name(item) for item in previous}:
                raise ValueError(f'名称或别名与“{existing["name"]}”重复')
        with self.db:
            if party_id is None:
                cursor = self.db.execute("INSERT INTO related_parties(name,aliases_json,note,roles_json,relation) VALUES(?,?,?,?,?)",
                    (name, json.dumps(aliases, ensure_ascii=False), note, json.dumps(roles, ensure_ascii=False), relation))
                return cursor.lastrowid
            cursor = self.db.execute("UPDATE related_parties SET name=?,aliases_json=?,note=?,roles_json=?,relation=? WHERE id=?",
                (name, json.dumps(aliases, ensure_ascii=False), note, json.dumps(roles, ensure_ascii=False), relation, party_id))
            if not cursor.rowcount:
                raise ValueError("所选尽调主体已不存在")
        return party_id

    def delete_related_party(self, party_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM related_parties WHERE id=?", (party_id,))

    def import_sheet(self, spec: ImportSpec, replace: bool = False) -> int:
        """按工作表事务写入；预检未通过（关键解析错误）时抛错回滚，保持旧数据不变。"""
        spec.validate()
        digest = file_hash(spec.path)
        headers = header_for_row(spec.path, spec.sheet, spec.header_row, spec.kind)
        formulas = formula_cells(spec.path, spec.sheet) if spec.path.suffix.lower() in (".xlsx", ".xlsm") else {}
        excluded: dict[str, int] = {}
        issues: list[dict] = []
        debit_total = credit_total = 0
        with self.db:
            conflicts = self.db.execute("SELECT id FROM sources WHERE (sha256=? OR original_path=?) AND sheet=? AND kind=?", (digest, str(spec.path.resolve()), spec.sheet, spec.kind)).fetchall()
            if conflicts and not replace:
                raise sqlite3.IntegrityError("该来源工作表已有导入记录")
            if replace:
                for old in conflicts:
                    self.db.execute("UPDATE jev_results SET stale=1 WHERE json_extract(local_json, '$.source_id')=?", (old["id"],))
                    self.db.execute("DELETE FROM entries WHERE source_id=?", (old["id"],))
                    self.db.execute("DELETE FROM balances WHERE source_id=?", (old["id"],))
                    self.db.execute("DELETE FROM report_rows WHERE source_id=?", (old["id"],))
                    self.db.execute("DELETE FROM sources WHERE id=?", (old["id"],))
            cur = self.db.execute(
                "INSERT INTO sources(kind,filename,original_path,sheet,sha256,mapping_json) VALUES(?,?,?,?,?,?)",
                (spec.kind, spec.path.name, str(spec.path.resolve()), spec.sheet, digest, json.dumps({"header_row": spec.header_row, "mapping": spec.mapping, "headers": [text_value(value) for value in headers]}, ensure_ascii=False)),
            )
            source_id = cur.lastrowid
            count = 0
            for decision in classify_rows(spec, formulas):
                if decision.disposition == DISPOSITION_PENDING:
                    raise ValueError(f"第 {decision.row_no} 行：{decision.reason}（未解决的关键解析错误，预检后再导入）")
                if decision.disposition != DISPOSITION_IMPORT:
                    label = EXCLUSION_LABELS.get(decision.disposition, decision.disposition)
                    excluded[label] = excluded.get(label, 0) + 1
                    continue
                row_no, data = decision.row_no, decision.data
                for warning in decision.warnings:
                    issues.append({"severity": "warning", "row_no": row_no, "reason": warning})
                if spec.kind in REPORT_KINDS:
                    self.db.execute(
                        "INSERT INTO report_rows(source_id,row_no,left_item,left_amount_1_cents,left_amount_2_cents,left_amount_3_cents,right_item,right_amount_1_cents,right_amount_2_cents,right_amount_3_cents,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, row_no, *(data[field] for field in ("left_item", "left_amount_1_cents", "left_amount_2_cents", "left_amount_3_cents", "right_item", "right_amount_1_cents", "right_amount_2_cents", "right_amount_3_cents", "raw_json"))),
                    )
                elif spec.kind == "entry":
                    debit_total += data["debit_cents"]
                    credit_total += data["credit_cents"]
                    code, name = data["account_code"], data["account_name"]
                    key = account_key(code, name)
                    self.db.execute("INSERT INTO accounts(key,code,name) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE accounts.name END", (key, code, name))
                    self.db.execute(
                        "INSERT INTO entries(source_id,row_no,entry_date,fiscal_year,period,voucher_no,voucher_type,account_key,summary,counterparty,auxiliary,currency,original_amount,maker,debit_cents,credit_cents,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, row_no, data["date"], data["fiscal_year"], data["period"], data["voucher_no"], data["voucher_type"], key, data["summary"], data["counterparty"], data["auxiliary"], data["currency"], data["original_amount"], data["maker"], data["debit_cents"], data["credit_cents"], data["raw_json"]),
                    )
                else:
                    code, name = data["account_code"], data["account_name"]
                    key = account_key(code, name)
                    self.db.execute("INSERT INTO accounts(key,code,name) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE accounts.name END", (key, code, name))
                    self.db.execute(
                        "INSERT INTO balances(source_id,row_no,account_key,opening_debit_cents,opening_credit_cents,period_debit_cents,period_credit_cents,year_debit_cents,year_credit_cents,closing_debit_cents,closing_credit_cents,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, row_no, key, *(data[field + "_cents"] for field in ("opening_debit", "opening_credit", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit")), data["raw_json"]),
                    )
                count += 1
            if count == 0:
                raise ValueError("没有识别到有效数据行，请检查表头和列映射")
            self.db.execute("UPDATE sources SET row_count=? WHERE id=?", (count, source_id))
            self.db.execute("DELETE FROM accounts WHERE key NOT IN (SELECT account_key FROM entries UNION SELECT account_key FROM balances)")
            verdict = f"成功：导入 {count} 行" + (f"；排除 {sum(excluded.values())} 行" if excluded else "")
            self.db.execute(
                "INSERT INTO import_prechecks(source_id,filename,sheet,kind,sha256,header_row,mapping_json,verdict,stats_json,issues_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source_id, spec.path.name, spec.sheet, spec.kind, digest, spec.header_row,
                 json.dumps({"mapping": spec.mapping, "excluded_rows": list(spec.excluded_rows)}, ensure_ascii=False),
                 verdict,
                 json.dumps({"imported_rows": count, "pending_rows": 0, "excluded": excluded,
                             "debit_cents": debit_total, "credit_cents": credit_total}, ensure_ascii=False),
                 json.dumps(issues, ensure_ascii=False)),
            )
            self._save_template(spec, headers)
        return count

    def _save_template(self, spec: ImportSpec, headers: list[str]) -> None:
        """经确认且成功导入的格式保存为本地模板；按表头结构签名去重，与文件名无关。"""
        signature = header_signature(headers)
        if not signature:
            return
        self.db.execute(
            """INSERT INTO import_templates(kind,sheet_label,header_signature,mapping_json)
               VALUES(?,?,?,?)
               ON CONFLICT(kind, header_signature) DO UPDATE SET
                 used_count=used_count+1, last_used_at=CURRENT_TIMESTAMP""",
            (spec.kind, spec.sheet[:80], signature,
             json.dumps({"mapping": spec.mapping, "header_row": spec.header_row}, ensure_ascii=False)),
        )

    def list_templates(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM import_templates ORDER BY last_used_at DESC,id DESC").fetchall()

    def template_candidates(self) -> list[dict]:
        """供 classify_sheet 做模板优先匹配的轻量结构。"""
        return [{"kind": row["kind"], "header_signature": row["header_signature"], "mapping": json.loads(row["mapping_json"])["mapping"]} for row in self.list_templates()]

    def precheck_for_source(self, source_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM import_prechecks WHERE source_id=?", (source_id,)).fetchone()

    def export_import_report(self, path: Path) -> int:
        from .precheck import build_import_report

        return build_import_report(self, path)

    def sources(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM sources ORDER BY id").fetchall()

    def delete_source(self, source_id: int) -> bool:
        """Remove one imported worksheet, preserving the source file and voucher marks."""
        with self.db:
            source = self.db.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None:
                return False
            self.db.execute("UPDATE jev_results SET stale=1 WHERE json_extract(local_json, '$.source_id')=?", (source_id,))
            for table in ("entries", "balances", "report_rows"):
                self.db.execute(f"DELETE FROM {table} WHERE source_id=?", (source_id,))
            self.db.execute("DELETE FROM sources WHERE id=?", (source_id,))
            self.db.execute("DELETE FROM accounts WHERE key NOT IN (SELECT account_key FROM entries UNION SELECT account_key FROM balances)")
        return True

    @staticmethod
    def _account_clause(account: str | list[str] | None, include_descendants: bool = False, alias: str = "e") -> tuple[str, list[str]]:
        if account is None or account == "":
            return "", []
        if isinstance(account, list):
            if not account:
                return "0=1", []
            clauses, args = [], []
            for key in account:
                clause, values = LedgerStore._account_clause(key, include_descendants, alias)
                clauses.append(clause)
                args.extend(values)
            return "(" + " OR ".join(clauses) + ")", args
        if include_descendants and account.startswith("C:"):
            return f"substr({alias}.account_key,1,length(?))=?", [account, account]
        return f"{alias}.account_key=?", [account]

    def report_rows(self, source_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM report_rows WHERE source_id=? ORDER BY row_no,id", (source_id,)).fetchall()

    def periods(self) -> list[tuple[str, str]]:
        return [(row[0], row[1]) for row in self.db.execute("SELECT DISTINCT fiscal_year,period FROM entries WHERE fiscal_year<>'' AND period<>'' ORDER BY CAST(fiscal_year AS INTEGER),CAST(period AS INTEGER)")]

    def summary(self, search: str = "", balance_source_id: int | None = None) -> list[sqlite3.Row]:
        if balance_source_id is None:
            balance_source_id = self.db.execute("SELECT COALESCE(MAX(id),-1) FROM sources WHERE kind='balance'").fetchone()[0]
        pattern = f"%{search}%"
        return self.db.execute("""
          SELECT a.key,a.code,a.name,
          COALESCE(b.od,0) opening_debit,COALESCE(b.oc,0) opening_credit,
          COALESCE(b.pd,0) reported_debit,COALESCE(b.pc,0) reported_credit,
          COALESCE(b.yd,0) year_debit,COALESCE(b.yc,0) year_credit,
          COALESCE(b.cd,0) closing_debit,COALESCE(b.cc,0) closing_credit,
          COALESCE(e.debit,0) detail_debit,COALESCE(e.credit,0) detail_credit,
          COALESCE(e.n,0) detail_count, COALESCE(b.n,0) balance_count
          FROM accounts a
          LEFT JOIN (SELECT account_key,SUM(opening_debit_cents) od,SUM(opening_credit_cents) oc,
            SUM(period_debit_cents) pd,SUM(period_credit_cents) pc,
            SUM(year_debit_cents) yd,SUM(year_credit_cents) yc,
            SUM(closing_debit_cents) cd,SUM(closing_credit_cents) cc,COUNT(*) n
            FROM balances WHERE source_id=? GROUP BY account_key) b ON b.account_key=a.key
          LEFT JOIN (SELECT account_key,SUM(debit_cents) debit,SUM(credit_cents) credit,COUNT(*) n
            FROM entries GROUP BY account_key) e ON e.account_key=a.key
          WHERE a.code LIKE ? OR a.name LIKE ? ORDER BY a.code,a.name
        """, (balance_source_id, pattern, pattern)).fetchall()

    def entries(self, account: str | list[str] | None = None, search: str = "", limit: int = 3000, fiscal_year: str | None = None, period: str | None = None, include_descendants: bool = False, sort_by: str = "date", descending: bool = False) -> list[sqlite3.Row]:
        where = []
        args: list[object] = []
        account_sql, account_args = self._account_clause(account, include_descendants)
        if account_sql:
            where.append(account_sql)
            args.extend(account_args)
        if fiscal_year and period:
            where.append("e.fiscal_year=? AND e.period=?")
            args.extend((fiscal_year, period))
        if search:
            where.append("(a.code LIKE ? OR a.name LIKE ? OR e.summary LIKE ? OR e.voucher_no LIKE ? OR e.counterparty LIKE ? OR e.auxiliary LIKE ?)")
            args.extend([f"%{search}%"] * 6)
        clause = " WHERE " + " AND ".join(where) if where else ""
        return self.db.execute("""
          SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path FROM entries e
          JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
        """ + clause + " ORDER BY " + self._entry_order(sort_by, descending) + " LIMIT ?", (*args, limit)).fetchall()

    @staticmethod
    def _entry_order(sort_by: str, descending: bool) -> str:
        columns = {"date": "e.entry_date", "voucher": "e.voucher_type,CAST(e.voucher_no AS INTEGER),e.voucher_no",
                   "code": "a.code", "name": "a.name", "summary": "e.summary",
                   "debit": "e.debit_cents", "credit": "e.credit_cents"}
        direction = " DESC" if descending else " ASC"
        return ",".join(column + direction for column in columns.get(sort_by, "e.entry_date").split(",")) + ",e.id"

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
        if target["fiscal_year"] and target["period"]:
            where = "e.fiscal_year=? AND e.period=? AND e.voucher_type=? AND e.voucher_no=?"
            args = (target["fiscal_year"], target["period"], target["voucher_type"], target["voucher_no"])
        elif target["entry_date"]:
            where = "e.entry_date=? AND e.voucher_type=? AND e.voucher_no=? AND (e.fiscal_year='' OR e.period='')"
            args = (target["entry_date"], target["voucher_type"], target["voucher_no"])
        else:
            return [target]
        return self.db.execute("""SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path
            FROM entries e JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
            WHERE """ + where + " ORDER BY e.source_id,e.row_no", args).fetchall()

    def auxiliary_totals(self, account: str | list[str] | None = None, fiscal_year: str | None = None, period: str | None = None, include_descendants: bool = False) -> list[sqlite3.Row]:
        account_sql, account_args = self._account_clause(account, include_descendants)
        return self.db.execute("""SELECT COALESCE(NULLIF(e.auxiliary,''),NULLIF(e.counterparty,''),'未注明') label,
          SUM(e.debit_cents) debit,SUM(e.credit_cents) credit,COUNT(*) n
          FROM entries e WHERE """ + (account_sql if account_sql else "1=1") + """ AND (? IS NULL OR e.fiscal_year=?) AND (? IS NULL OR e.period=?)
          GROUP BY label ORDER BY n DESC,label LIMIT 1000""", (*account_args, fiscal_year, fiscal_year, period, period)).fetchall()

    def export_entries(self, path: Path, account: str | list[str] | None = None, search: str = "", fiscal_year: str | None = None, period: str | None = None, include_descendants: bool = False, sort_by: str = "date", descending: bool = False) -> int:
        import csv
        from .related_parties import related_party_match

        parties = self.related_parties()
        query = """SELECT e.*,a.code,a.name,s.filename,s.sheet FROM entries e
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id"""
        where = []
        args: list[object] = []
        account_sql, account_args = self._account_clause(account, include_descendants)
        if account_sql:
            where.append(account_sql)
            args.extend(account_args)
        if fiscal_year and period:
            where.append("e.fiscal_year=? AND e.period=?")
            args.extend((fiscal_year, period))
        if search:
            where.append("(a.code LIKE ? OR a.name LIKE ? OR e.summary LIKE ? OR e.voucher_no LIKE ? OR e.counterparty LIKE ? OR e.auxiliary LIKE ?)")
            args.extend([f"%{search}%"] * 6)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY " + self._entry_order(sort_by, descending)
        cursor = self.db.execute(query, args)
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["日期", "会计年度", "期间", "凭证字", "凭证号", "科目编码", "科目名称", "摘要", "往来单位", "辅助核算", "币别", "原币金额", "制单人", "借方金额", "贷方金额", "源文件", "工作表", "原行号", "用户标注关联方"])
            for row in cursor:
                writer.writerow([row["entry_date"], row["fiscal_year"], row["period"], row["voucher_type"], row["voucher_no"], row["code"], row["name"], row["summary"], row["counterparty"], row["auxiliary"], row["currency"], row["original_amount"], row["maker"], format_money(row["debit_cents"]), format_money(row["credit_cents"]), row["filename"], row["sheet"], row["row_no"], related_party_match(row, parties)])
                count += 1
        return count
