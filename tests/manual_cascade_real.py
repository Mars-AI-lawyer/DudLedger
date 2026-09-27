"""手工测试：对真实账套文件跑三层级联（本地规则 → Jev → DeepSeek），真实调用模型服务。

用法：python tests/manual_cascade_real.py <目录或文件>... [--out 结果.json]

与批量导入一致：每张表发送前 20 行 × 前 40 列 × 每格 60 字的预览给 Jev / DeepSeek；
Jev 通过即不调用 DeepSeek；预检关键错误或提示同样触发升级。
只读原文件；导入验证只在临时目录的新账套中执行，不修改用户数据。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ledger_viewer.deepseek_mapping import get_api_key as deepseek_key, suggest_mapping as deepseek_suggest
from ledger_viewer.import_cascade import suggest_cascade
from ledger_viewer.importer import (
    ImportSpec, classify_sheet, file_hash, header_for_row, is_cashflow_sheet, rows, sheet_names,
)
from ledger_viewer.jev_mapping import get_api_key as jev_key, suggest_mapping as jev_suggest
from ledger_viewer.precheck import SEVERITY_CRITICAL, SEVERITY_WARNING, precheck
from ledger_viewer.store import LedgerStore

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


class LayerLog:
    """包一层 suggest 函数，记录每层真实调用结果与耗时。"""

    def __init__(self, name: str, func):
        self.name, self.func = name, func
        self.result: dict | None = None

    def __call__(self, preview: dict, key: str):
        start = time.monotonic()
        try:
            result = self.func(preview, key)
        except Exception as exc:  # noqa: BLE001
            self.result = {"ok": False, "error": str(exc)[:400], "seconds": round(time.monotonic() - start, 1)}
            raise
        self.result = {
            "ok": True, "kind": result.kind, "header_row": result.header_row,
            "mapping": dict(result.mapping), "warnings": list(result.warnings),
            "seconds": round(time.monotonic() - start, 1),
        }
        return result


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--out"]
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    files = collect(args)
    if not files:
        print("未找到任何 xls/xlsx 文件")
        return 1

    keys = {"jev": jev_key(), "deepseek": deepseek_key()}
    print(f"密钥状态: Jev={'已配置' if keys['jev'] else '未配置'}  DeepSeek={'已配置' if keys['deepseek'] else '未配置'}", flush=True)

    results = []
    for path in files:
        try:
            names = sheet_names(path)
        except Exception as exc:  # noqa: BLE001
            results.append({"file": path.name, "sheet": "", "bucket": "读取失败", "error": str(exc)[:300]})
            print(f"✗ 读取失败 {path.name}: {exc}", flush=True)
            continue
        for sheet in names:
            entry: dict = {"file": path.name, "sheet": sheet}
            try:
                if is_cashflow_sheet(path, sheet):
                    entry["bucket"] = "现金流量表（不导入）"
                    results.append(entry)
                    print(f"⏭ {path.name}/{sheet}: 现金流量表，跳过", flush=True)
                    continue
                spec = classify_sheet(path, sheet, None)
                if spec is not None:
                    report = precheck(spec)
                    critical = sum(1 for i in report.issues if i.severity == SEVERITY_CRITICAL)
                    warning = sum(1 for i in report.issues if i.severity == SEVERITY_WARNING)
                    entry["local"] = {"kind": spec.kind, "header_row": spec.header_row,
                                      "critical": critical, "warning": warning,
                                      "issues": [f"{i.severity}:{i.reason[:120]}" for i in report.issues[:6]]}
                else:
                    entry["local"] = None
            except Exception as exc:  # noqa: BLE001
                entry["local"] = {"error": str(exc)[:300]}
                spec = None
            # 空表检测：完全无行直接归为空表，不必浪费模型调用
            try:
                row_count = sum(1 for _ in rows(path, sheet))
            except Exception:
                row_count = -1
            if row_count == 0:
                entry["bucket"] = "空表（无可导入数据，跳过）"
                results.append(entry)
                print(f"⏭ {path.name}/{sheet}: 空表", flush=True)
                continue

            jev_log = LayerLog("jev", jev_suggest)
            deepseek_log = LayerLog("deepseek", deepseek_suggest)
            print(f"→ 分析 {path.name}/{sheet}（本地={'通过' if spec is not None else '未识别'}）…", flush=True)
            try:
                preview_rows = None
                from ledger_viewer.jev_mapping import preview_sheet
                preview_rows = preview_sheet(path, sheet)
                result = suggest_cascade(path, sheet, preview_rows, keys, jev_log, deepseek_log)
                # DeepSeek 层被调用且成功时必然是它胜出（Jev 先过则 DeepSeek 不会被调用）
                winner = "deepseek" if (deepseek_log.result and deepseek_log.result["ok"]) else "jev"
                entry["jev"], entry["deepseek"] = jev_log.result, deepseek_log.result
                # 真实导入验证：写入临时账套
                spec2 = ImportSpec(path, sheet, result.header_row, result.kind, result.mapping)
                with tempfile.TemporaryDirectory() as tmp:
                    store = LedgerStore(Path(tmp) / "test.sqlite")
                    try:
                        count = store.import_sheet(spec2)
                    finally:
                        store.close()
                report = precheck(spec2)
                entry["bucket"] = f"{winner} 通过"
                entry["import_rows"] = count
                entry["precheck"] = {"imported": report.stats.imported_rows,
                                     "pending": report.stats.pending_rows,
                                     "excluded": sum(report.stats.excluded.values()),
                                     "debit_cents": report.stats.debit_cents,
                                     "credit_cents": report.stats.credit_cents}
                timing = f"Jev {entry['jev']['seconds']}s"
                if winner == "deepseek" and entry["deepseek"]:
                    timing += f" / DeepSeek {entry['deepseek']['seconds']}s"
                print(f"  ✓ {winner} 建议 + 导入 {count} 行（耗时 {timing}）", flush=True)
            except Exception as exc:  # noqa: BLE001
                entry["jev"], entry["deepseek"] = jev_log.result, deepseek_log.result
                entry["bucket"] = "需要返回 Agent"
                entry["cascade_error"] = str(exc)[:700]
                jev_txt = "未尝试" if jev_log.result is None else ("过" if jev_log.result["ok"] else jev_log.result["error"][:150])
                ds_txt = "未尝试" if deepseek_log.result is None else ("过" if deepseek_log.result["ok"] else deepseek_log.result["error"][:150])
                print(f"  ✗ 两层均失败 → Agent。Jev: {jev_txt} | DeepSeek: {ds_txt}", flush=True)
            results.append(entry)
        print(flush=True)

    # 汇总
    buckets: dict[str, list] = {}
    for entry in results:
        buckets.setdefault(entry["bucket"], []).append(entry)
    print("== 汇总 ==", flush=True)
    for bucket in ("jev 通过", "deepseek 通过", "需要返回 Agent", "空表（无可导入数据，跳过）",
                   "现金流量表（不导入）", "读取失败"):
        for entry in buckets.get(bucket, []):
            mark = {"jev 通过": "✓", "deepseek 通过": "✓", "需要返回 Agent": "✗"}.get(bucket, "⏭")
            extra = f"（导入 {entry['import_rows']} 行）" if "import_rows" in entry else ""
            print(f"{mark} [{bucket}] {entry['file']} / {entry['sheet']}{extra}", flush=True)

    if out_path:
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细已写入 {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
