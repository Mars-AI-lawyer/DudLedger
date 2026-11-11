"""Shared, read-only spreadsheet review for automatic and manual import UI."""

from dataclasses import dataclass, field

from .background import check_cancelled
from .import_preview import preview_for_ai
from .import_work import prepare_sheets
from .importer import ImportSpec, file_hash, header_for_row, merged_ranges
from .precheck import PrecheckResult


def suggest_columns(path, sheet, suggest, api_key, cancelled=lambda: False, *, header_row=None, kind=None):
    fingerprint = file_hash(path)
    preview = preview_for_ai(path, sheet, header_row, kind)
    if file_hash(path) != fingerprint:
        raise ValueError("读取预览期间原文件发生变化，请重新审查")
    if cancelled():
        raise ValueError("已取消分析")
    result = suggest(preview, api_key)
    if cancelled():
        raise ValueError("已取消分析")
    if file_hash(path) != fingerprint:
        raise ValueError("分析期间原文件发生变化，请重新审查")
    if type(result.header_row) is not int or result.header_row not in {row['row'] for row in preview['rows']}:
        raise ValueError("建议表头行不在发送范围")
    spec = ImportSpec(path, sheet, result.header_row, result.kind, result.mapping)
    spec.validate()
    if any(top <= spec.header_row < bottom and any(left <= col + 1 <= right for col in spec.mapping.values())
           for top, bottom, left, right in merged_ranges(path, sheet)):
        raise ValueError("表头结束行仍在合并区域中间，请重新审查或交给 Agent。")
    headers = header_for_row(path, sheet, spec.header_row, spec.kind)
    if not headers or any(type(col) is not int or not 0 <= col < len(headers) for col in spec.mapping.values()):
        raise ValueError("建议列超出工作表范围")
    return result


@dataclass
class SheetReview:
    spec: ImportSpec
    fingerprint: str
    headers: list
    report: PrecheckResult | None = None
    exclusions: dict = field(default_factory=dict)
    notices: tuple = ()
    problem: str = ""
    base_spec: ImportSpec | None = None

    @property
    def ready(self):
        return self.report is not None and not self.report.blocked and not self.problem


def review_sheet(path, sheet, cancelled, progress, *, suggest=None, api_key="", manual_spec=None,
                 header_row=None, kind=None):
    """Never write; model mappings must pass local precheck after the bounded preview."""
    check_cancelled(cancelled)
    fingerprint = file_hash(path)
    warnings, notices = (), ()
    if manual_spec is None:
        result = suggest_columns(path, sheet, suggest, api_key, cancelled.is_set,
                                 header_row=header_row, kind=kind)
        spec = ImportSpec(path, sheet, result.header_row, result.kind, result.mapping)
        warnings = tuple(result.warnings)
        notices = tuple(getattr(result, "notices", ()))
    else:
        spec = manual_spec
        spec.validate()
    headers = header_for_row(path, sheet, spec.header_row, spec.kind)
    if not headers or any(type(col) is not int or not 0 <= col < len(headers) for col in spec.mapping.values()):
        raise ValueError("设置列超出工作表范围")
    prepared, _, statuses, _ = prepare_sheets([(0, spec)], cancelled, progress)
    check_cancelled(cancelled)
    if file_hash(path) != fingerprint:
        raise ValueError("审查期间原文件发生变化，请重新审查")
    if not prepared:
        return SheetReview(spec, fingerprint, headers, notices=notices, base_spec=spec,
                           problem="；".join((*warnings, statuses.get(0, "本地预检未通过"))))
    base_spec = spec
    spec, report, exclusions = prepared[0]
    if report.fingerprint != fingerprint:
        raise ValueError("审查期间原文件发生变化，请重新审查")
    return SheetReview(spec, fingerprint, headers, report, exclusions, notices, "；".join(warnings), base_spec)
