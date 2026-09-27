"""手工测试：对真实账套文件跑完整导入识别链路（发现→分类→预检）。

用法：python tests/manual_test_real_files.py <目录或文件>...
不修改任何文件，只读。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger_viewer.importer import DISPOSITION_IMPORT, DISPOSITION_PENDING, FIELDS, discover_sheets
from ledger_viewer.precheck import SEVERITY_CRITICAL, SEVERITY_WARNING, precheck

EXCLUDE_NAMES = {".DS_Store"}


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.rglob("*")
                            if f.suffix.lower() in (".xls", ".xlsx", ".xlsm", ".csv")
                            and f.name not in EXCLUDE_NAMES)
        elif p.is_file():
            files.append(p)
    return files


def main() -> int:
    files = collect(sys.argv[1:])
    if not files:
        print("未找到任何 xls/xlsx 文件")
        return 1

    specs, skipped = discover_sheets(files)
    print(f"== 共 {len(files)} 个文件，识别出 {len(specs)} 张可用表，{len(skipped)} 张跳过 ==\n")

    print("-- 识别结果 --")
    for spec in specs:
        fields = ", ".join(f"{FIELDS[f][0] if f in FIELDS else f}" for f in sorted(spec.mapping, key=spec.mapping.get))
        print(f"[{spec.kind}] {spec.path.name} / {spec.sheet}  表头行={spec.header_row}")
        print(f"    列映射: {fields}")

    print("\n-- 跳过原因 --")
    for line in skipped:
        print(f"    {line}")

    print("\n-- 预检结果 --")
    failures = 0
    for spec in specs:
        try:
            result = precheck(spec)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"✗ {spec.path.name} / {spec.sheet}: 预检异常 {type(exc).__name__}: {exc}")
            continue
        stats = result.stats
        critical = [i for i in result.issues if i.severity == SEVERITY_CRITICAL]
        warnings = [i for i in result.issues if i.severity == SEVERITY_WARNING]
        ok = not critical
        if not ok:
            failures += 1
        mark = "✓" if ok else "✗"
        print(f"{mark} {spec.path.name} / {spec.sheet}  类型={spec.kind}")
        print(f"    扫描 {stats.rows_seen} 行：可导入 {stats.imported_rows}，待处理 {stats.pending_rows}，"
              f"排除 {sum(stats.excluded.values())}（{stats.excluded or '无'}）")
        if spec.kind == "entry" or stats.debit_cents or stats.credit_cents:
            print(f"    借方合计 {stats.debit_cents / 100:,.2f}，贷方合计 {stats.credit_cents / 100:,.2f}")
        for issue in critical[:5]:
            loc = f"（第 {issue.row_no} 行）" if issue.row_no else ""
            print(f"    [严重]{loc} {issue.reason}")
        for issue in warnings[:5]:
            loc = f"（第 {issue.row_no} 行）" if issue.row_no else ""
            print(f"    [提示]{loc} {issue.reason}")
        if len(critical) > 5:
            print(f"    ……另有 {len(critical) - 5} 条严重问题")
        if len(warnings) > 5:
            print(f"    ……另有 {len(warnings) - 5} 条提示")

    print(f"\n== 结论：{len(specs) - failures}/{len(specs)} 张表可干净导入，{failures} 张存在严重疑点（需人工确认）==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
