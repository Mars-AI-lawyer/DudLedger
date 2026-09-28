"""疑点行的自动排除：AI 分诊优先，程序规则兜底；判断结果随导入记录留痕。

导入预检中“无法解析”的行（关键疑点）在当前列映射下不可能写入账套，
去向只有两种：确认为非账簿数据（噪声）后排除，或修正列映射／原表后重导。
这里把该判断交给 AI（DeepSeek，逐行 junk／data）；AI 不可用或失败时按保守的
程序规则处理。被排除的行与理由都会写入预检报告，可事后核查。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .importer import ImportSpec, text_value
from .precheck import SEVERITY_CRITICAL

# 单次分诊的行数与单元格上限：与列建议预览同一量级，控制发送内容
MAX_TRIAGE_ROWS = 40
MAX_CELLS = 12
MAX_CELL_CHARS = 60
# 程序兜底规则：疑点行少量且占比低才自动排除；疑似结构变化（表内另一张表）必须人工处理
GUARD_MAX_ROWS = 20
GUARD_MIN_RATIO = 20  # 疑点行不得超过可导入行的 1/20
STRUCTURAL_MARKS = ("结构", "分区", "另一张表")


@dataclass
class TriageOutcome:
    """一次疑点行处理的结果：excluded 可安全排除，held 需要人工处理。"""

    spec: ImportSpec
    excluded: dict[int, str] = field(default_factory=dict)  # row_no → 理由（含来源）
    held: dict[int, str] = field(default_factory=dict)      # row_no → 保留原因
    ai_used: bool = False

    @property
    def resolved(self) -> bool:
        """全部疑点都有安全去向时才自动导入；有任何 held 行都交回人工。"""
        return not self.held


def program_guard(pending: dict[int, str], importable_rows: int) -> bool:
    """无 AI 时的保守规则：疑点行少且不涉结构变化才整体自动排除。"""
    if not pending:
        return True
    if any(any(mark in reason for mark in STRUCTURAL_MARKS) for reason in pending.values()):
        return False
    if len(pending) > GUARD_MAX_ROWS:
        return False
    if len(pending) > max(1, importable_rows // GUARD_MIN_RATIO):
        return False
    return True


def _triage_messages(spec: ImportSpec, pending: dict[int, str], excerpts: dict[int, str]) -> list[dict]:
    items = []
    for row_no, reason in sorted(pending.items()):
        items.append({"row": row_no, "reason": reason[:120], "cells": excerpts.get(row_no, "")[:400] or "（未取得原单元格）"})
    user = (
        f"工作表「{spec.sheet}」按第 {spec.header_row} 行表头导入时，以下 {len(items)} 行无法解析：\n"
        + json.dumps(items, ensure_ascii=False)
        + "\n\n请逐行判断它是 junk（非账簿数据：注释、落款、批注、无关文字或损坏的噪声行）还是 data（真实业务数据行）。\n"
        '严格只输出一个 JSON 对象：{"rows": [{"row": 行号整数, "action": "junk" 或 "data", "reason": "不超过30字的理由"}]}\n'
        "规则：\n"
        "- 只对给出的行号作判断；判不出或拿不准时选 data（宁可保留给人工，不要把真实分录当垃圾）。\n"
        "- 结构与主表不同的连续区域（可能是表内另一张表）选 data。\n"
    )
    return [{"role": "system", "content": "你是会计电子表格数据清洗助手。只输出一个 JSON 对象，不要输出任何其他文字。"},
            {"role": "user", "content": user}]


def _parse_triage(content: str, pending: dict[int, str]) -> dict[int, tuple[str, str]]:
    """解析 AI 回答；行号不在疑点集或动作不认识的一律保守按 data 处理。"""
    from .deepseek_mapping import _json_answer

    answer = _json_answer(content)
    raw_rows = answer.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("AI 未返回行级判断")
    labels: dict[int, tuple[str, str]] = {}
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        try:
            row_no = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        action = str(item.get("action", "")).strip().lower()
        reason = str(item.get("reason", "")).strip()[:60]
        if row_no in pending and action in ("junk", "data"):
            labels[row_no] = (action, reason or ("非账簿数据行" if action == "junk" else "真实数据行"))
    if not labels:
        raise ValueError("AI 返回的行号与疑点行不符")
    return labels


def triage_rows(spec: ImportSpec, pending: dict[int, str], excerpts: dict[int, str], api_key: str, chat=None) -> dict[int, tuple[str, str]]:
    """一次 DeepSeek 请求返回逐行 junk／data 判断；失败由调用方回退程序规则。"""
    from .deepseek_mapping import MODEL, _chat

    if len(pending) > MAX_TRIAGE_ROWS:
        raise ValueError(f"疑点行过多（{len(pending)} 行），不发送 AI，按程序规则处理")
    payload = {"model": MODEL, "messages": _triage_messages(spec, pending, excerpts),
               "stream": False, "response_format": {"type": "json_object"},
               "thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    return _parse_triage((chat or _chat)(api_key, payload), pending)


def resolve_pending(spec: ImportSpec, pending: dict[int, str], importable_rows: int,
                    labels: dict[int, tuple[str, str]] | None = None) -> TriageOutcome:
    """统一入口：labels 为 AI 判断（None＝AI 未参与，走程序兜底）。"""
    outcome = TriageOutcome(spec=spec)
    if not pending:
        return outcome
    if labels is None:
        if program_guard(pending, importable_rows):
            outcome.excluded = {row: f"程序规则自动排除：{reason}" for row, reason in pending.items()}
        else:
            outcome.held = {row: f"疑点行较多或涉及结构变化，需人工确认：{reason}" for row, reason in pending.items()}
        return outcome
    outcome.ai_used = True
    for row, reason in pending.items():
        action, ai_reason = labels.get(row, ("data", "AI 未判断，保守保留"))
        if action == "junk":
            outcome.excluded[row] = f"AI 判断为非账簿数据（{ai_reason}）；原疑点：{reason}"
        else:
            outcome.held[row] = f"AI 判断为真实数据行（{ai_reason}），需修正列映射或原表后重导；原疑点：{reason}"
    return outcome


def pending_reasons(result) -> dict[int, str]:
    """从预检结果提取待处理行号 → 原因。"""
    reasons = {row_no: "" for row_no in result.pending_rows}
    for issue in result.issues:
        if issue.row_no in reasons and issue.severity == SEVERITY_CRITICAL:
            reasons[issue.row_no] = issue.reason
    return {row: reason or "无法解析" for row, reason in reasons.items()}


def row_excerpts(spec: ImportSpec, wanted: set[int]) -> dict[int, str]:
    """读取指定行的原始单元格摘录（与预检疑点表同口径）。"""
    from .importer import rows

    excerpts: dict[int, str] = {}
    if not wanted:
        return excerpts
    last = max(wanted)
    try:
        for row_no, row in rows(spec.path, spec.sheet):
            if row_no in wanted:
                cells = [text_value(value)[:MAX_CELL_CHARS] for value in row[:MAX_CELLS]]
                excerpts[row_no] = " | ".join(cell for cell in cells if cell) or "（空行）"
            if row_no >= last:
                break
    except Exception:
        pass
    return excerpts
