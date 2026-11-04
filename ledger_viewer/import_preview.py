"""Build a minimal, header-only cloud payload; workbook data stays local."""

from pathlib import Path

from .importer import (FIELDS, REPORT_KINDS, classify_sheet, guess_header, header_for_row,
                       scan_window, text_value)

MAX_COLUMNS = 40
MAX_CELL_CHARS = 60
PRIVACY_NOTICE = "仅发送本地定位的 1 行表头（多层表头在本地合并），最多 40 列、每格 60 字；不发送业务数据行、疑点行、文件名、工作表名或路径。"


def preview_headers(path: Path, sheet: str, header_row=None, kind=None) -> dict:
    """Locate headers locally and send one logical row, never sample records."""
    if header_row is not None:
        headers = header_for_row(path, sheet, header_row, kind or "entry")
    else:
        spec = classify_sheet(path, sheet)
        if spec is not None:
            header_row, kind = spec.header_row, spec.kind
            headers = header_for_row(path, sheet, header_row, kind)
        else:
            candidates = [guess_header(path, sheet, name)
                          for name in ("entry", "balance", *REPORT_KINDS)]
            header_row, headers, mapping = max(candidates, key=lambda item: len(item[2]))
            if len(mapping) < 2:
                # Unknown labels can still be offered to the model, but only a
                # text-only header row. Titles and mixed business rows stay local.
                header_row, headers = 0, []
                for number, row in scan_window(path, sheet):
                    nonempty = [value for value in row if text_value(value)]
                    labels = {name.lower() for aliases in FIELDS.values() for name in aliases}
                    labels.update(("编号", "名称", "增加", "减少", "余额", "行次", "序号"))
                    anchors = sum(text_value(value).lower() in labels for value in nonempty)
                    if anchors >= 2 and all(
                        isinstance(value, str) and len(value.strip()) <= MAX_CELL_CHARS
                        and not any(char.isdigit() for char in value)
                        and not any(mark in value for mark in ("：", ":", "公司", "有限公司"))
                        for value in nonempty
                    ):
                        header_row, headers = number, row
                        break
    if not header_row or not any(text_value(value) for value in headers):
        raise ValueError("无法在本地定位表头；请先手动填写表头行号，再使用 DeepSeek 建议列。")
    return {"sheet": "工作表", "rows": [{"row": header_row,
            "cells": [text_value(value)[:MAX_CELL_CHARS] for value in headers[:MAX_COLUMNS]]}]}
