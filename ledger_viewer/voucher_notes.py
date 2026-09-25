"""Local voucher annotations and deterministic Markdown export."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .importer import format_money

if TYPE_CHECKING:
    from .store import LedgerStore


def voucher_key(row) -> str:
    if row["voucher_no"] and row["fiscal_year"] and row["period"]:
        parts = ["period", row["fiscal_year"], row["period"], row["voucher_type"], row["voucher_no"]]
    elif row["voucher_no"] and row["entry_date"]:
        parts = ["date", row["entry_date"], row["voucher_type"], row["voucher_no"]]
    else:
        parts = ["row", row["original_path"], row["sheet"], row["row_no"]]
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def voucher_lines_for_key(store: LedgerStore, key: str) -> list:
    try:
        parts = json.loads(key)
    except (TypeError, ValueError):
        return []
    base = """SELECT e.*,a.code,a.name,s.filename,s.sheet,s.original_path,s.mapping_json
        FROM entries e JOIN accounts a ON a.key=e.account_key
        JOIN sources s ON s.id=e.source_id WHERE """
    if len(parts) == 5 and parts[0] == "period":
        condition, args = "e.fiscal_year=? AND e.period=? AND e.voucher_type=? AND e.voucher_no=?", parts[1:]
    elif len(parts) == 4 and parts[0] == "date":
        condition, args = "e.entry_date=? AND e.voucher_type=? AND e.voucher_no=? AND (e.fiscal_year='' OR e.period='')", parts[1:]
    elif len(parts) == 4 and parts[0] == "row":
        condition, args = "s.original_path=? AND s.sheet=? AND e.row_no=?", parts[1:]
    else:
        return []
    return store.db.execute(base + condition + " ORDER BY e.source_id,e.row_no,e.id", args).fetchall()


def voucher_signature(lines: list) -> str:
    fields = ("original_path", "sheet", "row_no", "raw_json", "entry_date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_key", "summary", "counterparty", "auxiliary", "currency", "original_amount", "maker", "debit_cents", "credit_cents")
    payload = [[row[field] for field in fields] for row in lines]
    payload.sort(key=lambda values: (values[0], values[1], values[2]))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def save_mark(store: LedgerStore, entry_id: int, note: str) -> str:
    row = store.entry(entry_id)
    if row is None:
        raise ValueError("所选分录已不存在")
    key = voucher_key(row)
    lines = voucher_lines_for_key(store, key)
    if not lines:
        raise ValueError("凭证分录已不存在")
    signature = voucher_signature(lines)
    with store.db:
        store.db.execute("""INSERT INTO voucher_marks(voucher_key,note,signature) VALUES(?,?,?)
            ON CONFLICT(voucher_key) DO UPDATE SET note=excluded.note,signature=excluded.signature,
            updated_at=CURRENT_TIMESTAMP""", (key, note, signature))
    return key


def save_mark_by_key(store: LedgerStore, key: str, note: str) -> None:
    lines = voucher_lines_for_key(store, key)
    if not lines:
        raise ValueError("原凭证已无法匹配；请先找到当前凭证再标注")
    if voucher_key(lines[0]) != key:
        raise ValueError("凭证标识已变化；请从当前明细重新标注，原注释仍保留供查阅")
    signature = voucher_signature(lines)
    with store.db:
        store.db.execute("UPDATE voucher_marks SET note=?,signature=?,updated_at=CURRENT_TIMESTAMP WHERE voucher_key=?", (note, signature, key))


def remove_mark(store: LedgerStore, key: str) -> None:
    with store.db:
        store.db.execute("DELETE FROM voucher_marks WHERE voucher_key=?", (key,))


def mark_for_entry(store: LedgerStore, entry_id: int):
    row = store.entry(entry_id)
    if row is None:
        return None
    return store.db.execute("SELECT * FROM voucher_marks WHERE voucher_key=?", (voucher_key(row),)).fetchone()


def list_marks(store: LedgerStore) -> list[dict]:
    result = []
    for mark in store.db.execute("SELECT * FROM voucher_marks ORDER BY created_at,voucher_key"):
        lines = voucher_lines_for_key(store, mark["voucher_key"])
        status = "未匹配" if not lines else ("内容变化" if voucher_signature(lines) != mark["signature"] else "正常")
        result.append({"key": mark["voucher_key"], "note": mark["note"], "status": status, "lines": lines})
    return result


def _inline(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, "\\" + character)
    return text


def _label(lines: list) -> str:
    first = lines[0]
    scope = f'{first["fiscal_year"]}年{first["period"]}期' if first["fiscal_year"] and first["period"] else first["entry_date"]
    voucher = f'{first["voucher_type"]}{first["voucher_no"]}' if first["voucher_no"] else f'无凭证号（来源第 {first["row_no"]} 行）'
    return f"{scope} {voucher}".strip()


def source_account(row) -> tuple[str, str]:
    try:
        mapping = json.loads(row["mapping_json"])["mapping"]
        original = json.loads(row["raw_json"])
        code_index, name_index = mapping.get("account_code"), mapping.get("account_name")
        code = str(original[code_index]).strip() if code_index is not None and code_index < len(original) else row["code"]
        name = str(original[name_index]).strip() if name_index is not None and name_index < len(original) else row["name"]
        return code or row["code"], name or row["name"]
    except (KeyError, TypeError, ValueError, IndexError):
        return row["code"], row["name"]


def render_marks_md(store: LedgerStore) -> tuple[str, int, int]:
    marks = list_marks(store)
    valid = [mark for mark in marks if mark["status"] == "正常"]
    skipped = len(marks) - len(valid)
    sections = ["# 关注凭证", "", f"账套：{_inline(store.path.name)}", "", "以下凭证依据已导入明细重组，来源行可用于回查原文件。", "", "## 科目索引", ""]
    index: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for number, mark in enumerate(valid, 1):
        label = _label(mark["lines"])
        for line in mark["lines"]:
            account = source_account(line)
            bucket = index.setdefault(account, [])
            if not any(existing[0] == number for existing in bucket):
                bucket.append((number, label))
    for code, name in sorted(index):
        sections.append(f"### {_inline(code)} {_inline(name)}".rstrip())
        sections.append("")
        for number, label in index[(code, name)]:
            sections.append(f"- [{_inline(label)}](#voucher-{number})")
        sections.append("")
    if not valid:
        sections += ["暂无可导出的关注凭证。", ""]
    sections += ["## 凭证记录", ""]
    for number, mark in enumerate(valid, 1):
        lines = mark["lines"]
        sections += [f'<a id="voucher-{number}"></a>', f"### {number}. {_inline(_label(lines))}", ""]
        debit = sum(line["debit_cents"] for line in lines)
        credit = sum(line["credit_cents"] for line in lines)
        dates = "、".join(dict.fromkeys(line["entry_date"] for line in lines if line["entry_date"])) or "未注明"
        sections += [f"- 凭证日期：{_inline(dates)}；已导入分录：{len(lines)} 行；借方合计 {format_money(debit)} 元；贷方合计 {format_money(credit)} 元。", ""]
        for line in lines:
            source = f'{line["filename"]}/{line["sheet"]}:{line["row_no"]}'
            code, name = source_account(line)
            extras = "".join(f"｜{title}：{_inline(line[field])}" for title, field in (("往来单位", "counterparty"), ("辅助核算", "auxiliary"), ("制单人", "maker")) if line[field])
            sections.append(f'- **{_inline(code)} {_inline(name)}**｜摘要：{_inline(line["summary"])}｜借方 {format_money(line["debit_cents"])}｜贷方 {format_money(line["credit_cents"])}{extras}｜来源：{_inline(source)}')
        sections += ["", "**注释**", ""]
        note = mark["note"].strip()
        sections += [*(f"> {line}" if line else ">" for line in note.splitlines())] if note else ["> （无）"]
        sections.append("")
    if skipped:
        sections += [f"> 另有 {skipped} 条标注因凭证未匹配或内容变化暂未导出，请在软件中复核。", ""]
    return "\n".join(sections), len(valid), skipped


def export_marks_md(store: LedgerStore, path: Path) -> tuple[int, int]:
    content, count, skipped = render_marks_md(store)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return count, skipped
