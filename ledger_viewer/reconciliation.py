"""Explicit, source-scoped comparison of supplied statements and ledger figures."""

from __future__ import annotations

import json
from collections import defaultdict

from .importer import format_money


SETTING_KEY = "reconciliation_plan"


def empty_plan() -> dict:
    return {"year": "", "period": "", "entry_source_ids": [], "balance_source_id": None,
            "balance_sheet": {"source_id": None, "amount_column": 1},
            "income_statement": {"source_id": None, "amount_column": 1}, "mappings": []}


def load_plan(store) -> dict:
    try:
        value = json.loads(store.setting(SETTING_KEY, "{}"))
    except (TypeError, ValueError):
        return empty_plan()
    result = empty_plan()
    if isinstance(value, dict):
        result.update({key: value[key] for key in result if key in value})
    return result


def save_plan(store, plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ValueError("核对方案无效")
    year, period = str(plan.get("year", "")), str(plan.get("period", ""))
    if not (year.isdigit() and len(year) == 4 and period.isdigit() and 1 <= int(period) <= 13):
        raise ValueError("请填写四位会计年度和 1—13 的会计期间")
    sources = {row["id"]: row for row in store.sources()}
    entry_ids = plan.get("entry_source_ids", [])
    if not isinstance(entry_ids, list) or not entry_ids or any(type(source_id) is not int or source_id not in sources or sources[source_id]["kind"] != "entry" for source_id in entry_ids):
        raise ValueError("请至少选择一份有效明细来源")
    balance_id = plan.get("balance_source_id")
    if balance_id is not None and (balance_id not in sources or sources[balance_id]["kind"] != "balance"):
        raise ValueError("余额表来源已失效")
    for kind in ("balance_sheet", "income_statement"):
        selected = plan.get(kind, {})
        source_id = selected.get("source_id")
        if source_id is not None and (source_id not in sources or sources[source_id]["kind"] != kind):
            raise ValueError("报表来源已失效")
        if selected.get("amount_column") not in (1, 2, 3):
            raise ValueError("报表金额列只能选第 1 至第 3 列")
    account_keys = {row["key"] for row in store.db.execute("SELECT key FROM accounts")}
    for mapping in plan.get("mappings", []):
        if mapping.get("kind") not in ("balance_sheet", "income_statement") or mapping.get("side") not in ("left", "right"):
            raise ValueError("报表项目映射无效")
        if mapping.get("source_id") != plan.get(mapping["kind"], {}).get("source_id"):
            raise ValueError("项目映射的报表来源已变化，请重新确认")
        if type(mapping.get("row_no")) is not int or mapping["row_no"] < 1:
            raise ValueError("报表行号无效")
        if mapping.get("direction") not in ("debit", "credit") or not mapping.get("accounts"):
            raise ValueError("请指定科目和金额方向")
        if any(account not in account_keys for account in mapping["accounts"]):
            raise ValueError("报表项目映射中有已失效科目")
    store.save_setting(SETTING_KEY, json.dumps(plan, ensure_ascii=False))


def _caption(source) -> str:
    return f'{source["filename"]}/{source["sheet"]}'


def _amount(row, side: str, column: int):
    return row[f"{side}_amount_{column}_cents"]


def run_reconciliation(store) -> list:
    from .checks import Finding

    if not store.setting(SETTING_KEY):
        return []
    plan = load_plan(store)
    findings = []
    sources = {row["id"]: row for row in store.sources()}
    entry_ids = plan.get("entry_source_ids", [])
    year, period = plan.get("year", ""), plan.get("period", "")
    selected_entries = [sources.get(source_id) for source_id in entry_ids]
    if not year or not period or not selected_entries or any(source is None or source["kind"] != "entry" for source in selected_entries):
        return [Finding("待核", "核对设置", "来源或期间未确认", "请重新选择明细来源和会计期间；现有明细仍可查看。")]
    period_label = f"{year}年{period}期"
    placeholders = ",".join("?" for _ in entry_ids)
    entry_totals = {row["account_key"]: row for row in store.db.execute(f"""
        SELECT account_key,SUM(debit_cents) debit,SUM(credit_cents) credit,COUNT(*) n FROM entries
        WHERE source_id IN ({placeholders}) AND fiscal_year=? AND period=? GROUP BY account_key
    """, (*entry_ids, year, period))}
    balance_source = sources.get(plan.get("balance_source_id"))
    balance_totals = {}
    if balance_source and balance_source["kind"] == "balance":
        groups = defaultdict(lambda: {"period_debit": 0, "period_credit": 0, "closing_debit": 0, "closing_credit": 0, "rows": []})
        for row in store.db.execute("SELECT * FROM balances WHERE source_id=?", (balance_source["id"],)):
            value = groups[row["account_key"]]
            for field in ("period_debit", "period_credit", "closing_debit", "closing_credit"):
                value[field] += row[field + "_cents"]
            value["rows"].append(row["row_no"])
        balance_totals = dict(groups)
        balance_mapping = json.loads(balance_source["mapping_json"]).get("mapping", {})
        if "period_debit" in balance_mapping and "period_credit" in balance_mapping:
            entry_caption = "、".join(_caption(source) for source in selected_entries)
            accounts = {row["key"]: row for row in store.db.execute("SELECT key,code,name FROM accounts")}
            for key in sorted(set(balance_totals) | set(entry_totals)):
                reported, detail = balance_totals.get(key), entry_totals.get(key)
                label = accounts[key]["code"] or accounts[key]["name"]
                if not reported or not detail:
                    findings.append(Finding("待核", "余额表与明细", label, f"{period_label}：一侧缺少该科目；余额表 {_caption(balance_source)}，明细 {entry_caption}。请确认覆盖范围及科目级次。"))
                    continue
                for side, label_side in (("debit", "借方"), ("credit", "贷方")):
                    left, right = reported["period_" + side], detail[side]
                    if left != right:
                        findings.append(Finding("差异", "余额表与明细", label, f"{period_label}本期{label_side}：余额表 {format_money(left)} 元，明细汇总 {format_money(right)} 元，差额 {format_money(left-right)} 元；来源：{_caption(balance_source)} 行 {reported['rows']} ↔ {entry_caption}。"))
        else:
            findings.append(Finding("待核", "余额表与明细", _caption(balance_source), "所选余额表未映射本期借方和贷方发生额，无法比较明细发生额。"))
    elif plan.get("balance_source_id") is not None:
        findings.append(Finding("待核", "核对设置", "余额表来源失效", "所选余额表可能已重新导入，请重新选择。"))

    for kind, title in (("balance_sheet", "资产负债表"), ("income_statement", "利润表")):
        selection = plan.get(kind, {})
        source_id = selection.get("source_id") if isinstance(selection, dict) else None
        if source_id is None:
            continue
        source = sources.get(source_id)
        if source is None or source["kind"] != kind:
            findings.append(Finding("待核", "核对设置", f"{title}来源失效", "所选报表可能已重新导入，请重新选择并确认项目映射。"))
            continue
        column = selection.get("amount_column", 1)
        metadata = json.loads(source["mapping_json"])
        rows = {row["row_no"]: row for row in store.report_rows(source_id)}
        mappings = [item for item in plan.get("mappings", []) if item.get("kind") == kind and item.get("source_id") == source_id]
        if not mappings:
            findings.append(Finding("待核", "报表项目映射", _caption(source), f"{title}尚未确认项目与科目的对应关系。"))
        else:
            mapped_positions = {(item["row_no"], item["side"]) for item in mappings}
            pending = sum(
                row[f"{side}_item"] != "" and _amount(row, side, column) is not None and (row["row_no"], side) not in mapped_positions
                for row in rows.values() for side in (("left", "right") if kind == "balance_sheet" else ("left",))
                if f"{side}_amount_{column}" in metadata.get("mapping", {})
            )
            if pending:
                findings.append(Finding("待核", "报表项目映射", _caption(source), f"{title}还有 {pending} 个有金额的项目未确认对应科目，暂未参与跨表核对。"))
        for mapping in mappings:
            row = rows.get(mapping["row_no"])
            side = mapping["side"]
            if row is None or side not in ("left", "right") or f"{side}_amount_{column}" not in metadata.get("mapping", {}):
                findings.append(Finding("待核", "报表项目映射", f"{title}第 {mapping['row_no']} 行", "报表行或所选金额列已失效，请重新确认。"))
                continue
            report_amount = _amount(row, side, column)
            item_name = row[f"{side}_item"]
            subject = f"{title}·{item_name or '未命名项目'}"
            if report_amount is None:
                findings.append(Finding("待核", "报表项目映射", subject, "原报表金额为空白，不能当作零值比较。"))
                continue
            account_keys = mapping["accounts"]
            direction = 1 if mapping["direction"] == "debit" else -1
            direction_label = "借方－贷方" if direction == 1 else "贷方－借方"
            account_names = ", ".join(account_keys)
            if kind == "balance_sheet":
                if not balance_source or any(key not in balance_totals for key in account_keys):
                    findings.append(Finding("待核", "资产负债表与余额表", subject, "所选科目在余额表中缺失，或未选择有效余额表；请核对范围。"))
                    continue
                computed = direction * sum(balance_totals[key]["closing_debit"] - balance_totals[key]["closing_credit"] for key in account_keys)
                category, comparison = "资产负债表与余额表", f"余额表 {_caption(balance_source)} 期末余额"
            else:
                for basis, totals, debit_field, credit_field, caption in (
                    ("余额表", balance_totals, "period_debit", "period_credit", _caption(balance_source) if balance_source else ""),
                    ("明细", entry_totals, "debit", "credit", "、".join(_caption(item) for item in selected_entries)),
                ):
                    if basis == "余额表" and not balance_source:
                        continue
                    if any(key not in totals for key in account_keys):
                        findings.append(Finding("待核", f"利润表与{basis}", subject, f"所选科目在{basis}中缺失；请核对范围。"))
                        continue
                    if basis == "余额表" and not all(field in json.loads(balance_source["mapping_json"])["mapping"] for field in ("period_debit", "period_credit")):
                        findings.append(Finding("待核", "利润表与余额表", subject, "余额表未映射本期借贷发生额，无法比较。"))
                        continue
                    computed = direction * sum(totals[key][debit_field] - totals[key][credit_field] for key in account_keys)
                    if report_amount != computed:
                        findings.append(Finding("差异", f"利润表与{basis}", subject, f"{period_label}：报表第 {column} 列 {format_money(report_amount)} 元，{basis} {format_money(computed)} 元，差额 {format_money(report_amount-computed)} 元；科目 {account_names} 按{direction_label}计算；来源：{_caption(source)}:{row['row_no']} ↔ {caption}。"))
                continue
            if report_amount != computed:
                findings.append(Finding("差异", category, subject, f"{period_label}：报表第 {column} 列 {format_money(report_amount)} 元，{comparison} {format_money(computed)} 元，差额 {format_money(report_amount-computed)} 元；科目 {account_names} 按{direction_label}计算；来源：{_caption(source)}:{row['row_no']}。"))
    return findings
