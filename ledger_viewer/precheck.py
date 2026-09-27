"""整表预检：正式写入账套前完整解析所选表格，集中呈现疑点。

预检不改动账套业务数据；解析问题（关键错误会阻止该表导入）与原账簿自身的差异
（合计口径等）分开呈现，后者只提示待核，不阻止导入、不自动修改账目。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from .importer import (
    DISPOSITION_EXCLUDED,
    DISPOSITION_IMPORT,
    DISPOSITION_PENDING,
    DISPOSITION_SUBTOTAL,
    EXCLUSION_LABELS,
    ImportSpec,
    REPORT_KINDS,
    RowDecision,
    classify_rows,
    file_hash,
    formula_cells,
    rows,
    text_value,
)


SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# 未映射列的“疑似金额”判定：非空单元格中可识别为金额且非零的比例
AMOUNT_RATIO = 0.6
AMOUNT_MIN_CELLS = 20
# 展示给界面的排除行样本上限
SAMPLE_LIMIT = 200


@dataclass(frozen=True)
class PrecheckIssue:
    """疑点：原单元格 → 当前解释 → 问题原因 → 可选处理。"""

    severity: str
    reason: str
    row_no: int | None = None
    column: str = ""
    raw: str = ""
    interpretation: str = ""
    action: str = ""


@dataclass
class PrecheckStats:
    header_row: int = 0
    imported_rows: int = 0
    pending_rows: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    debit_cents: int = 0
    credit_cents: int = 0
    first_row: int | None = None
    last_row: int | None = None
    rows_seen: int = 0

    @property
    def excluded_total(self) -> int:
        return sum(self.excluded.values())

    def exclusion_line(self) -> str:
        parts = [f"{EXCLUSION_LABELS.get(key, key)} {count} 行" for key, count in sorted(self.excluded.items(), key=lambda item: -item[1]) if count]
        return "、".join(parts)

    def to_json(self) -> dict:
        return {
            "header_row": self.header_row, "imported_rows": self.imported_rows, "pending_rows": self.pending_rows,
            "excluded": self.excluded, "debit_cents": self.debit_cents, "credit_cents": self.credit_cents,
            "first_row": self.first_row, "last_row": self.last_row, "rows_seen": self.rows_seen,
        }

    @staticmethod
    def from_json(payload: dict) -> "PrecheckStats":
        stats = PrecheckStats()
        for name in ("header_row", "imported_rows", "pending_rows", "debit_cents", "credit_cents", "rows_seen"):
            setattr(stats, name, int(payload.get(name, 0) or 0))
        stats.excluded = {str(key): int(value) for key, value in (payload.get("excluded") or {}).items()}
        first, last = payload.get("first_row"), payload.get("last_row")
        stats.first_row = int(first) if first else None
        stats.last_row = int(last) if last else None
        return stats


@dataclass
class PrecheckResult:
    spec: ImportSpec
    fingerprint: str
    stats: PrecheckStats
    issues: list[PrecheckIssue]
    pending_rows: list[int] = field(default_factory=list)
    excluded_samples: list[RowDecision] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.pending_rows) or self.stats.imported_rows == 0

    @property
    def needs_attention(self) -> bool:
        return bool(self.pending_rows) or bool(self.issues)

    def summary_line(self) -> str:
        stats = self.stats
        line = f"拟导入 {stats.imported_rows} 行"
        if self.spec.kind == "entry":
            from .importer import format_money

            line += f"（借方合计 {format_money(stats.debit_cents, comma=True)} / 贷方合计 {format_money(stats.credit_cents, comma=True)}）"
        if stats.first_row:
            line += f"，来源行 {stats.first_row}–{stats.last_row}"
        if stats.excluded_total:
            line += f"；排除 {stats.excluded_total} 行（{stats.exclusion_line()}）"
        return line

    def to_json(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "stats": self.stats.to_json(),
            "issues": [{
                "severity": issue.severity, "reason": issue.reason, "row_no": issue.row_no,
                "column": issue.column, "raw": issue.raw, "interpretation": issue.interpretation, "action": issue.action,
            } for issue in self.issues],
            "pending_rows": self.pending_rows,
            "excluded": {decision.row_no: decision.reason for decision in self.excluded_samples},
        }


def _row_excerpt(row: list[object], limit: int = 6) -> str:
    cells = [text_value(value)[:18] for value in row[:limit]]
    return " | ".join(cell for cell in cells if cell) or "（空行）"


def _raw_excerpts(spec: ImportSpec, wanted: set[int]) -> dict[int, str]:
    """单次扫描收集指定行的原单元格摘录，供疑点表展示“原单元格”。"""
    excerpts: dict[int, str] = {}
    if not wanted:
        return excerpts
    last = max(wanted)
    try:
        for row_no, row in rows(spec.path, spec.sheet):
            if row_no in wanted:
                excerpts[row_no] = _row_excerpt(row)
            if row_no >= last:
                break
    except Exception:
        pass
    return excerpts


def precheck(spec: ImportSpec) -> PrecheckResult:
    """完整解析所选表格：每一行都有去向；关键解析错误记为 critical 疑点。"""
    fingerprint = file_hash(spec.path)
    formulas = formula_cells(spec.path, spec.sheet) if spec.path.suffix.lower() in (".xlsx", ".xlsm") else {}
    stats = PrecheckStats(header_row=spec.header_row)
    issues: list[PrecheckIssue] = []
    pending_rows: list[int] = []
    samples: list[RowDecision] = []
    header_labels = _header_labels(spec)

    debit_total = credit_total = 0
    pending_decisions: list[RowDecision] = []
    warning_decisions: list[tuple[RowDecision, str]] = []
    for decision in classify_rows(spec, formulas):
        stats.rows_seen += 1
        if decision.disposition == DISPOSITION_IMPORT:
            stats.imported_rows += 1
            stats.first_row = stats.first_row or decision.row_no
            stats.last_row = decision.row_no
            if spec.kind == "entry" and decision.data:
                debit_total += decision.data["debit_cents"]
                credit_total += decision.data["credit_cents"]
            for warning in decision.warnings:
                warning_decisions.append((decision, warning))
        elif decision.disposition == DISPOSITION_PENDING:
            stats.pending_rows += 1
            pending_rows.append(decision.row_no)
            pending_decisions.append(decision)
        elif decision.disposition == DISPOSITION_SUBTOTAL and spec.kind == "entry" and decision.subtotal_cents and decision.subtotal_cents != (None, None):
            sheet_debit, sheet_credit = decision.subtotal_cents
            if sheet_debit is not None and sheet_debit != debit_total:
                from .importer import format_money

                issues.append(PrecheckIssue(SEVERITY_WARNING,
                                            f"原表第 {decision.row_no} 行合计借方 {format_money(sheet_debit, comma=True)}，与拟导入借方合计 {format_money(debit_total, comma=True)} 不一致",
                                            row_no=decision.row_no, interpretation="照常导入，差异仅提示待核",
                                            action="可能包含不导入的行或口径不同；请核对，不自动修改账目"))
            elif sheet_debit is not None and sheet_debit == debit_total and sheet_credit == credit_total:
                issues.append(PrecheckIssue(SEVERITY_INFO, f"与原表第 {decision.row_no} 行合计一致", row_no=decision.row_no))
        else:
            label = EXCLUSION_LABELS.get(decision.disposition, decision.disposition)
            stats.excluded[label] = stats.excluded.get(label, 0) + 1
            if decision.disposition != DISPOSITION_EXCLUDED and len(samples) < SAMPLE_LIMIT:
                samples.append(decision)
    stats.debit_cents, stats.credit_cents = debit_total, credit_total

    excerpts = _raw_excerpts(spec, {decision.row_no for decision in pending_decisions} | {decision.row_no for decision, _ in warning_decisions})
    for decision in pending_decisions:
        issues.append(PrecheckIssue(SEVERITY_CRITICAL, decision.reason, row_no=decision.row_no,
                                    raw=excerpts.get(decision.row_no, ""), interpretation="暂不导入，等待处理",
                                    action="可排除该行、返回调整列设置，或修正原表后重新预检"))
    for decision, warning in warning_decisions:
        issues.append(PrecheckIssue(SEVERITY_WARNING, warning, row_no=decision.row_no,
                                    raw=excerpts.get(decision.row_no, ""), interpretation="按当前列映射导入",
                                    action="请核对原表；也可返回调整列设置"))

    if spec.kind not in REPORT_KINDS:
        issues.extend(_unmapped_amount_columns(spec, header_labels))
    if stats.imported_rows == 0 and stats.pending_rows == 0:
        issues.append(PrecheckIssue(SEVERITY_CRITICAL, "没有识别到任何可导入的数据行，请检查表头和列映射", action="返回调整表头行或列设置"))
    return PrecheckResult(spec, fingerprint, stats, issues, pending_rows, samples)


def _header_labels(spec: ImportSpec) -> dict[int, str]:
    from .importer import header_for_row

    try:
        headers = header_for_row(spec.path, spec.sheet, spec.header_row, spec.kind)
    except Exception:
        return {}
    return {index: f"第 {index + 1} 列「{name or '(空)'}」" for index, name in enumerate(headers)}


def _unmapped_amount_columns(spec: ImportSpec, header_labels: dict[int, str]) -> list[PrecheckIssue]:
    """未映射但看起来装着金额的列：可能是漏掉的借贷/余额列，提示核对。"""
    issues: list[PrecheckIssue] = []
    mapped = set(spec.mapping.values())
    stats: dict[int, list[int]] = {}
    try:
        for _row_no, row in rows(spec.path, spec.sheet):
            for index in range(min(len(row), 60)):
                if index in mapped:
                    continue
                value = row[index]
                if value is None or str(value).strip() in ("", "—", "-", "–"):
                    continue
                try:
                    cents = _plain_money(value)
                except ValueError:
                    stats.setdefault(index, [0, 0])[0] += 1
                    continue
                counter = stats.setdefault(index, [0, 0])
                counter[0] += 1
                if cents != 0:
                    counter[1] += 1
    except Exception:
        return issues
    for index, (non_empty, non_zero) in sorted(stats.items()):
        if non_empty >= AMOUNT_MIN_CELLS and non_zero >= AMOUNT_MIN_CELLS * 0.5 and non_empty >= 1 and non_zero / non_empty >= AMOUNT_RATIO:
            issues.append(PrecheckIssue(SEVERITY_WARNING,
                                        f"{header_labels.get(index, f'第 {index + 1} 列')}未映射，但 {non_empty} 个非空单元格大多像金额",
                                        column=header_labels.get(index, f"第 {index + 1} 列"),
                                        interpretation="该列不会导入", action="若是借贷/余额/原币金额列，请返回补映射"))
    return issues


def _plain_money(value: object) -> int:
    from .importer import money

    return money(value)


def build_import_report(store, path: Path) -> int:
    """导出中文导入质量报告（Markdown），便于事后核查。"""
    from .importer import format_money

    lines = ["# DudLedger 导入质量报告", "", f"生成时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}", f"账套文件：{store.path.name}", ""]
    sources = store.sources()
    if not sources:
        lines.append("账套中还没有导入记录。")
    else:
        kind_labels = {"entry": "明细账/序时账", "balance": "科目余额表", "balance_sheet": "资产负债表", "income_statement": "利润表"}
        lines += ["## 来源清单", "", "| # | 文件 | 工作表 | 类型 | 有效行 | 导入时间 | 预检结论 |", "|---|---|---|---|---|---|---|"]
        legacy = 0
        for source in sources:
            record = store.precheck_for_source(source["id"])
            if record is None:
                verdict = "无历史预检报告（旧版导入）"
                legacy += 1
            else:
                verdict = record["verdict"]
            lines.append(f'| {source["id"]} | {source["filename"]} | {source["sheet"]} | {kind_labels.get(source["kind"], source["kind"])} | {source["row_count"]} | {source["imported_at"]} | {verdict} |')
        lines.append("")
        lines += ["## 各表预检明细", ""]
        for source in sources:
            record = store.precheck_for_source(source["id"])
            title = f'### {source["filename"]} / {source["sheet"]}'
            if record is None:
                lines += [title, "", "无历史预检报告（旧版导入，不追认新校验）。", ""]
                continue
            import json as _json

            try:
                stats = PrecheckStats.from_json(_json.loads(record["stats_json"]))
            except (ValueError, TypeError):
                stats = PrecheckStats()
            try:
                issues = _json.loads(record["issues_json"]) or []
            except (ValueError, TypeError):
                issues = []
            lines += [title, "", f"- 预检结论：{record['verdict']}", f"- 拟导入 {stats.imported_rows} 行；待处理 {stats.pending_rows} 行；排除 {stats.excluded_total} 行（{stats.exclusion_line() or '无'}）"]
            if source["kind"] == "entry":
                lines.append(f"- 借方合计 {format_money(stats.debit_cents, comma=True)}；贷方合计 {format_money(stats.credit_cents, comma=True)}")
            lines.append(f"- 文件指纹（SHA-256）：{record['sha256'][:16]}…")
            if issues:
                lines += ["- 疑点："]
                for issue in issues[:50]:
                    row = f"第 {issue.get('row_no')} 行 " if issue.get("row_no") else ""
                    lines.append(f"  - [{issue.get('severity', 'info')}] {row}{issue.get('reason', '')}")
            lines.append("")
        if legacy:
            lines.append(f"注：{legacy} 条旧版导入记录没有历史预检报告；它们保持原样，不追认已通过新校验。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(sources)
