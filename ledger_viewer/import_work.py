"""Local import operations that never access Tk or cloud services."""

from .background import Cancelled, check_cancelled
from .importer import (classify_sheet, is_cashflow_sheet, sheet_names,
                       header_for_row, guess_header, guess_mapping, rows, text_value)
from .precheck import precheck
from .row_triage import pending_reasons, resolve_pending


def requires_background(paths):
    paths = list(dict.fromkeys(paths))
    try:
        return len(paths) > 4 or sum(path.stat().st_size for path in paths) >= 256 * 1024
    except OSError:
        return True


def inventory(paths, templates, cancelled, report):
    items, skipped, errors = [], [], {}
    for path in dict.fromkeys(paths):
        check_cancelled(cancelled)
        report(f"正在读取 {path.name}")
        try:
            names = sheet_names(path)
        except Exception as exc:
            skipped.append(f"{path.name}：{exc}")
            continue
        for sheet in names:
            check_cancelled(cancelled)
            try:
                if is_cashflow_sheet(path, sheet):
                    continue
                spec = classify_sheet(path, sheet, templates)
            except Exception as exc:
                errors[len(items)] = f"识别失败：{exc}；可手动调整"
                spec = None
            items.append((path, sheet, spec))
    check_cancelled(cancelled)
    return items, skipped, errors


def prepare_sheets(items, cancelled, report):
    prepared, held, statuses, updated = [], [], {}, {}
    for index, spec in items:
        check_cancelled(cancelled)
        label = f"{spec.path.name}/{spec.sheet}"
        report(f"预检 {label}")
        try:
            result = precheck(spec, cancelled=cancelled,
                progress=lambda count: report(f"预检 {label}：{count:,} 行"))
            reasons = {}
            if result.pending_rows:
                outcome = resolve_pending(spec, pending_reasons(result), result.stats.imported_rows)
                if not outcome.resolved:
                    detail = "；".join(f"第 {issue.row_no} 行：{issue.reason}" for issue in result.issues[:3])
                    raise ValueError(f"{len(outcome.held)} 行疑点未解决；{detail}")
                reasons = outcome.excluded
                if reasons:
                    spec = spec.with_extra_exclusions(tuple(reasons))
                    result = precheck(spec, cancelled=cancelled)
            if result.blocked:
                raise ValueError("没有可导入的数据行，请检查表头和列设置")
            updated[index] = spec
            prepared.append((spec, result, reasons))
        except Cancelled:
            raise
        except Exception as exc:
            check_cancelled(cancelled)
            statuses[index] = str(exc)
            held.append(spec)
    return prepared, held, statuses, updated


def preview_data(path, sheet, kind, manual, row_no, cancelled, *, auto_kind=False, templates=None, mapping=None):
    if auto_kind:
        spec = classify_sheet(path, sheet, templates)
        if spec:
            kind = spec.kind
    check_cancelled(cancelled)
    if manual:
        if row_no < 1:
            raise ValueError("表头行号必须大于 0")
        headers = header_for_row(path, sheet, row_no, kind)
        guess = guess_mapping(headers, kind)
    else:
        row_no, headers, guess = guess_header(path, sheet, kind)
    if not headers:
        raise ValueError("找不到表头")
    if mapping is not None and any(index < 0 or index >= len(headers) for index in mapping.values()):
        raise ValueError("原表列数发生变化，请重新识别表头并核对列设置")
    lines = []
    omitted = False
    for number, line in rows(path, sheet):
        check_cancelled(cancelled)
        if number <= 5 or number >= row_no - 2:
            lines.append(f"{number:>4}  " + " | ".join(text_value(value)[:26] if value is not None and str(value).strip() else "〔空〕" for value in line[:14]))
        elif not omitted:
            lines.append("…（省略中间行）")
            omitted = True
        if number >= row_no + 5:
            break
    check_cancelled(cancelled)
    return row_no, headers, mapping if mapping is not None else guess, "\n".join(lines), kind
