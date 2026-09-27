"""Escalate column recognition only after local validation; never write a book.

通过标准是“不劣于本地规则的识别结果”：模型建议产生的预检问题若不多于、不重于
本地基线，就视为建议可用；本地规则没有识别出来的表，只阻断关键解析错误。
模型关于列取舍的说明性提示不参与判定，真正的数据问题始终由预检和导入确认把关。
"""

import re

from .importer import ImportSpec, classify_sheet, file_hash, header_for_row
from .precheck import precheck, SEVERITY_CRITICAL, SEVERITY_WARNING


_SEVERITY_RANK = {SEVERITY_WARNING: 1, SEVERITY_CRITICAL: 2}


def redact_error(error, keys) -> str:
    message = str(error)
    for key in (keys.values() if isinstance(keys, dict) else [keys]):
        if key:
            message = message.replace(key, "[密钥已隐藏]")
    return message


def _issue_category(reason: str) -> str:
    """同一文件、同一解析器生成的问题文案里，可变部分（数字、原值）从第一个数字或冒号开始。"""
    head = re.split(r"[\d：:（(]", reason, maxsplit=1)[0].strip()
    return head or reason[:16]


def _issue_profile(issues) -> dict[str, tuple[int, int]]:
    """类别 ->（最高严重级，该级别条数）。"""
    profile: dict[str, tuple[int, int]] = {}
    for issue in issues:
        category = _issue_category(issue.reason)
        rank = _SEVERITY_RANK.get(issue.severity, 1)
        top, count = profile.get(category, (0, 0))
        if rank > top:
            profile[category] = (rank, 1)
        elif rank == top:
            profile[category] = (top, count + 1)
    return profile


def _excess_issues(issues, baseline_issues) -> list:
    """模型建议比本地基线更多或更严重的问题。严重级下降的类别视为改善，不再计较条数。"""
    base = _issue_profile(baseline_issues)
    model = _issue_profile(issues)
    excess = []
    for category, (rank, count) in model.items():
        base_rank, base_count = base.get(category, (0, 0))
        if rank > base_rank or (rank == base_rank and count > base_count):
            excess.extend(issue for issue in issues if _issue_category(issue.reason) == category)
    return excess


def _baseline_report(path, sheet):
    """本地规则在同一张表上的识别与预检结果；识别失败时没有基线。"""
    try:
        spec = classify_sheet(path, sheet, None)
        return precheck(spec) if spec is not None else None
    except Exception:
        return None


def suggest_cascade(path, sheet, preview, keys, jev, deepseek, cancelled=lambda: False):
    """Try each configured provider once. A failed cascade requires explicit repair."""
    fingerprint = file_hash(path)
    baseline = _baseline_report(path, sheet)
    failures = []
    for provider, label, suggest in (("jev", "Jev", jev), ("deepseek", "DeepSeek", deepseek)):
        if cancelled():
            raise ValueError("已取消分析")
        if not keys.get(provider):
            failures.append(f"{label}：尚未配置密钥")
            continue
        try:
            result = suggest(preview, keys[provider])
            spec = ImportSpec(path, sheet, result.header_row, result.kind, result.mapping)
            spec.validate()
            headers = header_for_row(path, sheet, result.header_row, result.kind)
            if not headers or any(type(col) is not int or not 0 <= col < len(headers) for col in result.mapping.values()):
                raise ValueError("建议列超出工作表范围")
            report = precheck(spec)
            if report.fingerprint != fingerprint or file_hash(path) != fingerprint:
                raise ValueError("分析期间原文件发生变化，请重新分析")
            if baseline is not None:
                excess = _excess_issues(report.issues, baseline.issues)
            else:
                excess = [issue for issue in report.issues if issue.severity == SEVERITY_CRITICAL]
            problems = list(result.warnings)
            problems.extend(issue.reason for issue in excess)
            if report.blocked and not problems and (baseline is None or not baseline.blocked):
                problems.append("没有可导入数据或仍有待处理行")
            if problems:
                raise ValueError("；".join(dict.fromkeys(problems))[:700])
            if cancelled():
                raise ValueError("已取消分析")
            return result
        except Exception as exc:
            failures.append(f"{label}：{redact_error(exc, keys)}")
    raise ValueError("\n".join(failures) + "\n可点击“复制给我的 Agent”继续处理，或手动调整列。")
