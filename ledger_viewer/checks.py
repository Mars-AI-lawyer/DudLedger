"""Reconciliation checks; never silently repair source accounting figures."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .importer import format_money
from .store import LedgerStore


@dataclass(frozen=True)
class Finding:
    level: str
    category: str
    subject: str
    detail: str


def run_checks(store: LedgerStore) -> list[Finding]:
    db = store.db
    findings: list[Finding] = []
    sources = store.sources()
    entry_sources = [s for s in sources if s["kind"] == "entry"]
    balance_sources = [s for s in sources if s["kind"] == "balance"]
    if len(entry_sources) > 1:
        findings.append(Finding("提示", "覆盖范围", "多份明细", "请确认不同文件的期间及范围没有重叠；系统不自动去重。"))
    if not entry_sources:
        findings.append(Finding("提示", "覆盖范围", "缺少明细", "未导入明细账或序时账，无法穿透至分录或重建凭证。"))
    if not balance_sources:
        findings.append(Finding("提示", "覆盖范围", "缺少余额表", "无法与企业提供的科目余额表核对。"))
    if len(balance_sources) > 1:
        findings.append(Finding("提示", "余额表来源", "多个余额表", "科目余额页只显示所选的一份余额表，不把各期余额相加；请确认其期间与明细一致。"))
    missing_voucher = db.execute("SELECT COUNT(*) FROM entries WHERE voucher_no=''").fetchone()[0]
    if missing_voucher:
        findings.append(Finding("提示", "凭证穿透", "凭证号缺失", f"{missing_voucher} 条分录没有凭证号，无法可靠归并为同一张凭证；请检查列映射和原表。"))

    for row in db.execute("""SELECT b.*,a.code,a.name,s.filename,s.sheet,s.mapping_json
            FROM balances b JOIN accounts a ON a.key=b.account_key JOIN sources s ON s.id=b.source_id"""):
        mapping = json.loads(row["mapping_json"])["mapping"]
        subject = f'{row["code"] or row["name"]} · {row["filename"]}/{row["sheet"]}:{row["row_no"]}'
        has_opening = "opening_balance" in mapping or "opening_debit" in mapping or "opening_credit" in mapping
        has_closing = "closing_balance" in mapping or "closing_debit" in mapping or "closing_credit" in mapping
        if has_opening and has_closing and "period_debit" in mapping and "period_credit" in mapping:
            expected = row["opening_debit_cents"] - row["opening_credit_cents"] + row["period_debit_cents"] - row["period_credit_cents"]
            actual = row["closing_debit_cents"] - row["closing_credit_cents"]
            if expected != actual:
                findings.append(Finding("差异", "余额滚动", subject, f"期初净额＋本期借方－贷方与期末净额差 {format_money(actual - expected)} 元。"))
        if row["closing_debit_cents"] and row["closing_credit_cents"]:
            findings.append(Finding("提示", "余额列", subject, "同一行期末借方与贷方均有金额，请核对原表含义。"))

    for row in db.execute("""SELECT e.*,a.code,a.name,s.filename,s.sheet FROM entries e
            JOIN accounts a ON a.key=e.account_key JOIN sources s ON s.id=e.source_id
            WHERE e.debit_cents<>0 AND e.credit_cents<>0"""):
        findings.append(Finding("提示", "分录金额", f'{row["code"] or row["name"]} · {row["filename"]}/{row["sheet"]}:{row["row_no"]}', "同一行借贷都有金额，请核对原表。"))

    scope = "CASE WHEN fiscal_year<>'' AND period<>'' THEN fiscal_year||'年'||period||'期' ELSE entry_date END"
    for row in db.execute(f"""SELECT {scope} voucher_scope,voucher_type,voucher_no,
            SUM(debit_cents) debit,SUM(credit_cents) credit,COUNT(*) n
            FROM entries WHERE voucher_no<>'' AND (entry_date<>'' OR (fiscal_year<>'' AND period<>''))
            GROUP BY voucher_scope,voucher_type,voucher_no HAVING debit<>credit"""):
        findings.append(Finding("待核", "凭证借贷", f'{row["voucher_scope"]} {row["voucher_type"]}{row["voucher_no"]}', f"已导入 {row['n']} 行，借方 {format_money(row['debit'])} 元，贷方 {format_money(row['credit'])} 元；可能是凭证不平，或所给明细未覆盖全部科目。"))

    for row in db.execute("""SELECT e.entry_date,e.fiscal_year,e.period,e.voucher_type,e.voucher_no,s.filename,e.row_no
            FROM entries e JOIN sources s ON s.id=e.source_id
            WHERE e.entry_date GLOB '????-??-??' AND e.fiscal_year<>'' AND e.period<>''
            AND (substr(e.entry_date,1,4)<>e.fiscal_year OR
              (CAST(substr(e.entry_date,6,2) AS INTEGER)<>CAST(e.period AS INTEGER) AND e.period<>'13'))"""):
        findings.append(Finding("提示", "期间与日期", f'{row["filename"]}:{row["row_no"]} {row["voucher_type"]}{row["voucher_no"]}', f'日期 {row["entry_date"]}，会计年度/期间 {row["fiscal_year"]}/{row["period"]}；请核对是否特殊会计期间。'))

    duplicates = db.execute(f"""SELECT {scope} voucher_scope,voucher_type,voucher_no,account_key,summary,debit_cents,credit_cents,
          COUNT(DISTINCT source_id) n FROM entries WHERE voucher_no<>''
          GROUP BY voucher_scope,voucher_type,voucher_no,account_key,summary,debit_cents,credit_cents
          HAVING n>1 LIMIT 100""").fetchall()
    for row in duplicates:
        findings.append(Finding("待核", "跨文件重复", f'{row["voucher_scope"]} {row["voucher_type"]}{row["voucher_no"]}', "不同导入文件出现相同分录特征；可能是重复导出，不自动删除。"))
    from .reconciliation import run_reconciliation
    findings.extend(run_reconciliation(store))
    return findings
