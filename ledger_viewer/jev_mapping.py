"""Optional Jev suggestions for spreadsheet columns. No import happens here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import keyring
from typesafe_sdk import Choice, TypeSafeClient

from .importer import ImportSpec, rows, text_value
from .jev_runtime import MODEL, REQUEST_SLOTS


KEYRING_SERVICE = "DudLedger Jev"
KEYRING_ACCOUNT = "typesafe_api_key"
PREVIEW_ROWS = 20
# 宽表按列分块发送：上限 40 列，超出的列不发送（本地规则仍可识别更多列）
PREVIEW_COLUMNS = 40

KIND_DESCRIPTIONS = {
    "entry": "序时账或明细账；每行是一条会计分录，常见日期、凭证号、科目、摘要、借方金额、贷方金额。",
    "balance": "科目余额表或试算平衡表；每行是一个科目，常见期初、本期发生、本年累计、期末的借贷余额。",
    "balance_sheet": "资产负债表；项目是资产、负债或所有者权益，通常有期末数和期初数。",
    "income_statement": "利润表；项目是收入、成本、费用或利润，通常有本期和上期金额。",
    "unknown": "无法从预览可靠判断，或是不支持的现金流量表等其他资料。",
}

FIELD_DESCRIPTIONS = {
    "date": "凭证或业务发生日期，不是报表日期。",
    "fiscal_year": "会计年度，通常是四位年份。",
    "period": "会计期间或月份。",
    "voucher_type": "凭证字、凭证类别，例如记、收、付。",
    "voucher_no": "凭证号或凭证编号。",
    "account_code": "会计科目编码或代码。",
    "account_name": "会计科目名称。",
    "summary": "分录摘要或业务说明。",
    "debit": "明细分录的借方发生金额；表格是“单列金额＋借贷方向”布局时选 none，不要选金额列。",
    "credit": "明细分录的贷方发生金额；表格是“单列金额＋借贷方向”布局时选 none，不要选金额列。",
    "amount": "单一金额列；表格没有借贷分列、金额只有一列时选它，必须同时选“direction”。",
    "counterparty": "往来单位、客户或供应商名称。",
    "auxiliary": "辅助核算或核算维度。",
    "currency": "币别或币种。",
    "original_amount": "原币或外币金额。",
    "maker": "制单人或录入人。",
    "opening_debit": "科目期初余额的借方列；不能选贷方列。",
    "opening_credit": "科目期初余额的贷方列；不能选借方列。",
    "opening_balance": "科目期初余额的单列金额；不是借贷分列。",
    "period_debit": "本期发生额的借方列；不是本年累计或期末余额。",
    "period_credit": "本期发生额的贷方列；不是本年累计或期末余额。",
    "year_debit": "本年累计发生额的借方列；不是本期发生额。",
    "year_credit": "本年累计发生额的贷方列；不是本期发生额。",
    "closing_debit": "科目期末余额的借方列；不能选贷方列。",
    "closing_credit": "科目期末余额的贷方列；不能选借方列。",
    "closing_balance": "科目期末余额的单列金额；不是借贷分列。",
    "direction": "借贷方向列（借/贷/平），或余额表期初与期末共用一个方向列；单列金额、单列余额必须选它。",
    "opening_direction": "余额表期初余额的方向列（借/贷/平）；期初与期末方向分成两列时选它，不要选期末方向列。",
    "closing_direction": "余额表期末余额的方向列（借/贷/平）；期初与期末方向分成两列时选它，不要选期初方向列。",
    "left_item": "左侧或单侧报表项目名称；不是金额。",
    "left_amount_1": "左侧项目右边第一列金额。",
    "left_amount_2": "左侧项目右边第二列金额。",
    "left_amount_3": "左侧项目右边第三列金额。",
    "right_item": "资产负债表右侧的负债及所有者权益项目名称。",
    "right_amount_1": "右侧项目右边第一列金额。",
    "right_amount_2": "右侧项目右边第二列金额。",
    "right_amount_3": "右侧项目右边第三列金额。",
}

KIND_FIELDS = {
    "entry": ("date", "fiscal_year", "period", "voucher_type", "voucher_no", "account_code", "account_name", "summary", "debit", "credit", "amount", "direction", "counterparty", "auxiliary", "currency", "original_amount", "maker"),
    "balance": ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "closing_balance", "direction", "opening_direction", "closing_direction"),
    "balance_sheet": ("left_item", "left_amount_1", "left_amount_2", "left_amount_3", "right_item", "right_amount_1", "right_amount_2", "right_amount_3"),
    "income_statement": ("left_item", "left_amount_1", "left_amount_2", "left_amount_3"),
}


@dataclass(frozen=True)
class MappingSuggestion:
    kind: str
    header_row: int
    mapping: dict[str, int]
    confidence: dict[str, float]
    warnings: tuple[str, ...]
    model: str
    # 说明性提示（如同列冲突的取舍），不影响导入，也不会阻塞 AI 建议的通过
    notices: tuple[str, ...] = ()


_SESSION_KEY = None
_MEMORY_ONLY = False


def _secure_backend() -> bool:
    # Never silently accept a plaintext/file backend selected by third-party plugins.
    try:
        module = type(keyring.get_keyring()).__module__
        return module in ('keyring.backends.macOS', 'keyring.backends.Windows', 'keyring.backends.SecretService', 'keyring.backends.kwallet')
    except Exception:
        return False


def get_api_key() -> str:
    global _MEMORY_ONLY
    if _SESSION_KEY is not None:
        return _SESSION_KEY
    try:
        if _secure_backend():
            value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ''
            _MEMORY_ONLY = False
            return value
    except Exception:
        pass
    _MEMORY_ONLY = True
    return ''


def key_memory_only() -> bool:
    return _MEMORY_ONLY


def save_api_key(value: str) -> None:
    global _SESSION_KEY, _MEMORY_ONLY
    value = value.strip()
    if not value or not value.isascii() or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError('API Key 不能为空，且只能包含不带空格的 ASCII 字符')
    try:
        if not _secure_backend():
            raise RuntimeError('No secure backend')
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
    except Exception:
        _SESSION_KEY, _MEMORY_ONLY = value, True
    else:
        _SESSION_KEY, _MEMORY_ONLY = None, False


def delete_api_key() -> None:
    global _SESSION_KEY, _MEMORY_ONLY
    try:
        if _secure_backend():
            if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT):
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        elif _SESSION_KEY is None or not _MEMORY_ONLY:
            raise RuntimeError('No secure backend')
    except Exception:
        _SESSION_KEY = ''
        raise ValueError('本次运行已停用密钥，但无法确认系统凭据库中的旧密钥已删除；请在系统凭据管理中检查。') from None
    _SESSION_KEY, _MEMORY_ONLY = '', False


def preview_sheet(path: Path, sheet: str) -> dict:
    """Bound the exact material sent to Jev; callers show it before transmission."""
    preview = []
    for row_no, row in rows(path, sheet):
        preview.append({"row": row_no, "cells": [text_value(value)[:60] for value in row[:PREVIEW_COLUMNS]]})
        if len(preview) >= PREVIEW_ROWS:
            break
    if not preview:
        raise ValueError("工作表为空，无法取得映射建议")
    return {"sheet": sheet[:100], "rows": preview}


def _answer(response, question: str, allowed: set[str]) -> tuple[str, float]:
    answer = response.choices.get(question)
    if answer is None or answer.choice not in allowed:
        raise ValueError(f"Jev 对“{question}”未返回有效选项")
    return answer.choice, float(answer.confidence)


def critical_warnings(kind: str, mapping: dict) -> list[str]:
    """Only surface suggestions that lose data the importer cannot reconstruct;
    everything else stays silent and is reviewable in the filled preview."""
    hints = []
    if kind == "entry" and ("debit" in mapping) != ("credit" in mapping):
        hints.append("借方或贷方金额只识别到一侧，请核对另一侧金额列")
    if kind == "entry" and "amount" in mapping and "direction" not in mapping:
        hints.append("识别到单一金额列但缺少“借贷方向”列；请确认方向列并补选，导入前必须补上")
    if kind == "balance" and "closing_balance" not in mapping and ("closing_debit" in mapping) != ("closing_credit" in mapping):
        hints.append("期末余额只识别到一侧借贷列，请核对")
    return hints


def suggest_mapping(preview: dict, api_key: str, client_factory=TypeSafeClient) -> MappingSuggestion:
    """Jev selects from supplied rows and columns; code validates every choice."""
    numbers = [int(item["row"]) for item in preview["rows"]]
    if not numbers:
        raise ValueError("没有可分析的预览行")
    headers = {f"r{number}": f"第 {number} 行" for number in numbers}
    headers["none"] = "预览中找不到可靠的表头"
    with REQUEST_SLOTS, client_factory(api_key=api_key, model=MODEL, timeout=25.0) as client:
        first = client.system_one(
            state=preview,
            questions={
                "kind": Choice(instructions="根据工作表名称和预览行，这张中国会计表格属于哪一类？不能可靠判断时选 unknown。", criteria=KIND_DESCRIPTIONS),
                "header": Choice(instructions="哪一行是字段列名所在的最后一行？如果是两层表头，选择借贷等子标题所在行；不要选公司名称或报表标题。", criteria=headers),
            },
        )
        kind, kind_confidence = _answer(first, "kind", set(KIND_DESCRIPTIONS))
        header_choice, header_confidence = _answer(first, "header", set(headers))
        if kind == "unknown" or header_choice == "none":
            raise ValueError("Jev 无法可靠确认表类型或表头，请使用手工映射")
        header_row = int(header_choice[1:])
        header_data = next(item for item in preview["rows"] if item["row"] == header_row)
        above = next((item["cells"] for item in preview["rows"] if item["row"] == header_row - 1), [])
        next_data = next((item["cells"] for item in preview["rows"] if item["row"] == header_row + 1), [])
        column_count = max(len(item["cells"]) for item in preview["rows"])
        criteria = {"none": "没有对应列；宁可空置，不要猜测。"}
        for index in range(column_count):
            current = header_data["cells"][index] if index < len(header_data["cells"]) else ""
            upper = above[index] if index < len(above) else ""
            sample = next_data[index] if index < len(next_data) else ""
            criteria[f"c{index + 1}"] = f"第 {index + 1} 列；上一行 {upper!r}；本行 {current!r}；下一行样例 {sample!r}"
        questions = {
            field: Choice(
                instructions=f"这张表已判断为 {KIND_DESCRIPTIONS[kind]} 请从列中选择“{field}”：{FIELD_DESCRIPTIONS[field]} 没有对应列选 none。"
                             + ("多币种余额表的发生额、余额请选本币（人民币）列，原币列留空。" if kind == "balance" else ""),
                criteria=criteria,
            )
            for field in KIND_FIELDS[kind]
        }
        second = client.system_one(state={"preview": preview, "kind": kind, "header_row": header_row}, questions=questions)
    confidence = {"kind": kind_confidence, "header": header_confidence}
    candidates = []
    for field in KIND_FIELDS[kind]:
        choice, score = _answer(second, field, set(criteria))
        confidence[field] = score
        if choice != "none":
            candidates.append((score, field, int(choice[1:]) - 1))
    # A source column may represent only one normalized field; conflicts are surfaced, not silently dropped.
    mapping = {}
    occupied = {}
    for score, field, index in sorted(candidates, reverse=True):
        if index in occupied:
            continue
        mapping[field] = index
        occupied[index] = field
    warnings = critical_warnings(kind, mapping)
    notices = []
    for _score, field, index in sorted(candidates, reverse=True):
        if field in mapping:
            continue
        loser = occupied.get(index, "?")
        notices.append(f"“{field}”与“{loser}”选择了同一列，已按置信度保留“{loser}”；请人工核对")
    try:
        ImportSpec(Path("preview.xlsx"), preview["sheet"], header_row, kind, mapping).validate()
    except ValueError as exc:
        warnings.append(f"当前建议尚不满足导入条件：{exc}")
    model = getattr(second, "model", MODEL)
    return MappingSuggestion(kind, header_row, mapping, confidence, tuple(warnings), str(model), tuple(notices))
