"""Workbook inspection and normalization. Original files are never changed.

导入可靠性约定：
- ``classify_rows`` 是行级去向的唯一裁判：每一行都会得到“导入 / 待处理 / 排除（含原因）”的结论；
  ``normalized_rows`` 只是它的严格包装（遇到待处理即抛错，保持旧调用兼容）。
- 金额、借贷方向、期间冲突、公式缓存缺失等关键问题标记为“待处理”，绝不静默填零或猜测。
- 表头识别结合实际合并单元格区域，扫描窗口不再限制在前 20 行。
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

from openpyxl import load_workbook
import xlrd


FIELDS = {
    "date": ("日期", "凭证日期", "业务日期", "记账日期", "date"),
    "fiscal_year": ("会计年度", "账簿年度", "年度", "年", "fiscal year"),
    "period": ("期间", "会计期间", "月份", "月", "period"),
    "voucher_no": ("凭证号", "凭证编号", "凭证号码", "凭证号数", "凭证字号", "voucher no", "voucher number"),
    "voucher_type": ("凭证字", "凭证类型", "凭证类别", "字", "voucher type"),
    "account_code": ("科目编码", "科目代码", "会计科目编码", "科目编号", "account code"),
    "account_name": ("科目名称", "科目全名", "会计科目", "科目", "account name"),
    "summary": ("摘要", "业务摘要", "说明", "description", "memo"),
    "debit": ("借方金额", "借方发生额", "本期借方", "借方", "debit"),
    "credit": ("贷方金额", "贷方发生额", "本期贷方", "贷方", "credit"),
    # 用友等导出的序时账只有一列“金额”，借贷由“方向”列给出
    "amount": ("金额", "发生金额", "发生额", "本币金额", "本位币金额", "金额（本币）", "amount"),
    "counterparty": ("往来单位", "对方单位", "客商", "客户", "供应商", "counterparty"),
    "auxiliary": ("辅助核算", "核算维度", "核算项目", "辅助项", "项目", "部门", "auxiliary"),
    "currency": ("币别", "币种", "货币", "currency"),
    "original_amount": ("原币金额", "外币金额", "原币", "original amount"),
    "maker": ("制单", "制单人", "录入人", "prepared by"),
    "opening_debit": ("期初借方", "期初余额借方", "期初借方余额", "年初借方", "opening debit"),
    "opening_credit": ("期初贷方", "期初余额贷方", "期初贷方余额", "年初贷方", "opening credit"),
    "period_debit": ("本期借方发生额", "借方本期发生额", "本期借方", "本期发生借方", "本期发生额借方", "借方发生额", "period debit"),
    "period_credit": ("本期贷方发生额", "贷方本期发生额", "本期贷方", "本期发生贷方", "本期发生额贷方", "贷方发生额", "period credit"),
    "year_debit": ("本年累计借方", "本年累计借方发生额", "本年借方累计", "year debit"),
    "year_credit": ("本年累计贷方", "本年累计贷方发生额", "本年贷方累计", "year credit"),
    "closing_debit": ("期末借方", "期末余额借方", "期末借方余额", "期末借方金额", "closing debit"),
    "closing_credit": ("期末贷方", "期末余额贷方", "期末贷方余额", "期末贷方金额", "closing credit"),
    "opening_balance": ("期初余额", "年初余额", "opening balance"),
    "closing_balance": ("期末余额", "closing balance"),
    "direction": ("余额方向", "借贷方向", "方向", "借或贷", "balance direction"),
}

REPORT_KINDS = ("balance_sheet", "income_statement")
REPORT_ITEM_HEADERS = {"项目", "项目名称", "报表项目", "科目", "名称", "item", "description"}
REPORT_AMOUNT_HEADERS = {"期末余额", "期初余额", "年初余额", "上年年末余额", "本期金额", "本年金额", "上期金额", "本年累计金额", "上年同期金额", "本年累计", "上年同期", "本期数", "上期数", "期末数", "期初数", "amount", "current", "prior"}

SUBTOTAL = re.compile(r"^(?:本月|本年|本期|全年|当月)?(?:合计|累计|小计|总计)$")
FOOTER = re.compile(r"^(?:第\s*\d+\s*页\b|共\s*\d+\s*页\b|打印(?:时间|日期)|制表(?:人|日期)|审核(?:人|日期))")

# 表头扫描窗口：解除“前 20 行”限制即可——使用者导入前会先手动整理表格，标题区不会太深；
# 扫太深既无必要，还会让深处另一张表的表头有机会抢过真表头。更深的表头可手动填行号。
HEADER_SCAN_ROWS = 30

# 行去向
DISPOSITION_IMPORT = "import"
DISPOSITION_PENDING = "pending"
DISPOSITION_HEADER = "skip_header"
DISPOSITION_TITLE = "skip_title"
DISPOSITION_BLANK = "skip_blank"
DISPOSITION_SUBTOTAL = "skip_subtotal"
DISPOSITION_FOOTER = "skip_footer"
DISPOSITION_DIMENSION = "skip_dimension"
DISPOSITION_ZERO = "skip_zero"
DISPOSITION_EXCLUDED = "skip_excluded"
DISPOSITION_RANGE = "skip_range"

EXCLUSION_LABELS = {
    DISPOSITION_HEADER: "表头",
    DISPOSITION_TITLE: "标题",
    DISPOSITION_BLANK: "空行",
    DISPOSITION_SUBTOTAL: "小计/合计",
    DISPOSITION_FOOTER: "页脚",
    DISPOSITION_DIMENSION: "维度子行",
    DISPOSITION_ZERO: "无金额行",
    DISPOSITION_EXCLUDED: "人工排除",
    DISPOSITION_RANGE: "数据区域外",
}


@dataclass(frozen=True)
class RowDecision:
    """一行数据的去向结论；``pending`` 表示关键解析错误，导入前必须处理。"""

    row_no: int
    disposition: str
    reason: str = ""
    data: dict | None = None
    warnings: tuple[str, ...] = ()
    subtotal_cents: tuple[int, int] | None = None


def _norm(value: object) -> str:
    return re.sub(r"[\s_（）()\-:：]+", "", str(value or "")).lower()


def money(value: object) -> int:
    if value is None or str(value).strip() in ("", "—", "-", "–"):
        return 0
    if isinstance(value, bool):
        raise ValueError("金额不能为布尔值")
    raw = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "")
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"无法识别金额：{value}") from exc
    if not amount.is_finite():
        raise ValueError(f"金额不是有限数：{value}")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(cents: int, comma: bool = False) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{fraction:02d}" if comma else f"{sign}{whole}.{fraction:02d}"


def date_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    raw = str(value).strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            return dt.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


DATE_NORMALIZED = re.compile(r"\d{4}-\d{2}-\d{2}")


def year_text(value: object) -> str:
    raw = text_value(value)
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}(?:\.0+)?", raw):
        return raw[:4]
    raise ValueError(f"会计年度无法识别：{raw}")


def period_text(value: object) -> str:
    raw = text_value(value)
    if not raw:
        return ""
    if re.fullmatch(r"\d{1,2}(?:\.0+)?", raw):
        number = int(float(raw))
        if 1 <= number <= 13:
            return str(number)
    raise ValueError(f"会计期间无法识别：{raw}")


def period_from_date(date: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", date)
    return (match.group(1), str(int(match.group(2)))) if match else ("", "")


def text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return date_text(value)
    return str(value).strip()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# CSV 编码与分隔符嗅探


_CSV_CACHE: dict[tuple[str, int, int], tuple[str, str]] = {}


def _decode_prefix(data: bytes, encoding: str) -> str | None:
    """按给定编码解码前缀；错误若只出现在末尾（截断的多字节字符）则少量裁剪重试。"""
    for cut in (0, 1, 2, 3):
        chunk = data[: len(data) - cut] if cut else data
        try:
            return chunk.decode(encoding)
        except UnicodeDecodeError as exc:
            if exc.start < len(chunk) - 4:
                return None
            continue
    return None


def detect_csv(path: Path) -> tuple[str, str]:
    """常见中文 Excel 导出的 CSV 依次尝试 utf-8-sig / utf-16(BOM) / gb18030，分隔符由抽样决定。"""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CSV_CACHE.get(key)
    if cached:
        return cached
    with path.open("rb") as handle:
        head = handle.read(65536)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
        sample = head.decode("utf-16", errors="ignore")
    else:
        encoding = "gb18030"
        sample = None
        for candidate in ("utf-8-sig", "gb18030"):
            sample = _decode_prefix(head, candidate)
            if sample is not None:
                encoding = candidate
                break
        if sample is None:
            sample = head.decode("gb18030", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    result = (encoding, delimiter)
    _CSV_CACHE[key] = result
    return result


def sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        return ["CSV"]
    if path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            return book.sheet_names()
        finally:
            book.release_resources()
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("只支持 .xlsx、.xlsm、.xls、.csv")
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def rows(path: Path, sheet: str) -> Iterator[tuple[int, list[object]]]:
    if path.suffix.lower() == ".csv":
        encoding, delimiter = detect_csv(path)
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            for index, row in enumerate(reader, 1):
                yield index, list(row)
        return
    if path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            if sheet not in book.sheet_names():
                raise ValueError(f"工作表不存在：{sheet}")
            page = book.sheet_by_name(sheet)
            for index in range(page.nrows):
                values = []
                for cell in page.row(index):
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        values.append(None)
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        values.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
                        values.append(int(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        values.append(bool(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        raise ValueError(f"{sheet} 第 {index + 1} 行包含 Excel 错误单元格")
                    else:
                        values.append(cell.value)
                yield index + 1, values
        finally:
            book.release_resources()
        return
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in book.sheetnames:
            raise ValueError(f"工作表不存在：{sheet}")
        for index, row in enumerate(book[sheet].iter_rows(values_only=True), 1):
            yield index, list(row)
    finally:
        book.close()


def formula_cells(path: Path, sheet: str) -> dict[tuple[int, int], str]:
    """xlsx 公式坐标表（data_only=False 一次扫描）。openpyxl 不计算公式；
    若文件未保存计算缓存，data_only 读取会得到空值，必须作为疑点暴露。"""
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return {}
    book = load_workbook(path, read_only=True, data_only=False)
    found: dict[tuple[int, int], str] = {}
    try:
        if sheet not in book.sheetnames:
            return found
        for row_no, row in enumerate(book[sheet].iter_rows(), 1):
            for col_no, cell in enumerate(row):
                if cell.data_type == "f" and cell.value is not None:
                    found[(row_no, col_no)] = str(cell.value)
    finally:
        book.close()
    return found


# ---------------------------------------------------------------------------
# 合并单元格与多层表头


_MERGED_CACHE: dict[tuple[str, int, int, str], list[tuple[int, int, int, int]]] = {}


def merged_ranges(path: Path, sheet: str) -> list[tuple[int, int, int, int]]:
    """返回 1 基、闭区间的合并区域 (row1,row2,col1,col2)。只读模式拿不到，需完整加载，故按文件缓存。"""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, sheet)
    cached = _MERGED_CACHE.get(key)
    if cached is not None:
        return cached
    ranges: list[tuple[int, int, int, int]] = []
    if path.suffix.lower() == ".csv":
        pass
    elif path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            if sheet in book.sheet_names():
                ranges = [(rlo + 1, rhi, clo + 1, chi) for rlo, rhi, clo, chi in book.sheet_by_name(sheet).merged_cells]
        finally:
            book.release_resources()
    else:
        book = load_workbook(path, read_only=False, data_only=True)
        try:
            if sheet in book.sheetnames:
                ranges = [(rng.min_row, rng.max_row, rng.min_col, rng.max_col) for rng in book[sheet].merged_cells.ranges]
        finally:
            book.close()
    if len(_MERGED_CACHE) > 8:
        _MERGED_CACHE.clear()
    _MERGED_CACHE[key] = ranges
    return ranges


_WINDOW_CACHE: dict[tuple[str, int, int, str], list[tuple[int, list[object]]]] = {}


def scan_window(path: Path, sheet: str) -> list[tuple[int, list[object]]]:
    """缓存表头扫描窗口内的原始行，供多种表类型/候选复用，避免重复读文件。"""
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, sheet)
    cached = _WINDOW_CACHE.get(key)
    if cached is not None:
        return cached
    window: list[tuple[int, list[object]]] = []
    for row_no, row in rows(path, sheet):
        window.append((row_no, row))
        if row_no >= HEADER_SCAN_ROWS:
            break
    if len(_WINDOW_CACHE) > 8:
        _WINDOW_CACHE.clear()
    _WINDOW_CACHE[key] = window
    return window


def _expanded_row(window_rows: dict[int, list[object]], ranges: list[tuple[int, int, int, int]], row_no: int) -> list[object]:
    row = list(window_rows.get(row_no, []))
    for row1, row2, col1, col2 in ranges:
        if row1 <= row_no <= row2:
            anchor = window_rows.get(row1, [])
            value = anchor[col1 - 1] if col1 - 1 < len(anchor) else None
            for col in range(col1 - 1, min(col2, max(len(row), col1))):
                while len(row) <= col:
                    row.append(None)
                row[col] = value
    return row


def combine_header(upper: list[object], lower: list[object]) -> list[str]:
    """按列拼接两层表头：上层组名 + 下层子标题；相同文本（纵向合并）只保留一份。
    “借方/贷方”子标题会继承左侧最近的上层组名，覆盖真实合并与纯留白两种画法。"""
    combined: list[str] = []
    group = ""
    for index in range(max(len(upper), len(lower))):
        top = text_value(upper[index]) if index < len(upper) else ""
        bottom = text_value(lower[index]) if index < len(lower) else ""
        if top:
            group = top
        if bottom and _norm(bottom) in ("借方", "贷方") and group and _norm(group) != _norm(bottom):
            combined.append(group + bottom)
        elif top and bottom:
            combined.append(bottom if _norm(top) == _norm(bottom) else top + bottom)
        else:
            combined.append(top or bottom)
    return combined


def header_signature(headers: list[str]) -> str:
    return "|".join(_norm(name) for name in headers if _norm(name))


def _window_maps(path: Path, sheet: str) -> tuple[dict[int, list[object]], list[tuple[int, int, int, int]]]:
    window_rows = {row_no: row for row_no, row in scan_window(path, sheet)}
    return window_rows, merged_ranges(path, sheet)


def header_candidates(path: Path, sheet: str, row_no: int, kind: str) -> list[list[str]]:
    """给定行的表头候选：平面行；账簿类再叠加“上一行组名 + 本行子标题”的组合候选。"""
    window_rows, ranges = _window_maps(path, sheet)
    flat = [text_value(value) for value in _expanded_row(window_rows, ranges, row_no)]
    if kind not in REPORT_KINDS and row_no > 1:
        upper = _expanded_row(window_rows, ranges, row_no - 1)
        combined = combine_header(upper, flat)
        if combined != flat:
            return [flat, combined]
    return [flat]


def _balance_header_pair(top: list[object], bottom: list[object]) -> list[str] | None:
    if len(guess_mapping([text_value(value) for value in top], "balance")) < 2 or sum(_norm(value) in ("借方", "贷方") for value in bottom) < 2:
        return None
    result = []
    current_group = ""
    for index in range(max(len(top), len(bottom))):
        upper = text_value(top[index]) if index < len(top) else ""
        lower = text_value(bottom[index]) if index < len(bottom) else ""
        if upper:
            current_group = upper
        if _norm(lower) in ("借方", "贷方") and current_group:
            result.append(current_group + lower)
        else:
            result.append(upper or lower)
    return result


def header_for_row(path: Path, sheet: str, row_no: int, kind: str) -> list[str]:
    candidates = header_candidates(path, sheet, row_no, kind)
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=lambda headers: len(guess_mapping(headers, kind)))


def guess_header(path: Path, sheet: str, kind: str) -> tuple[int, list[str], dict[str, int]]:
    best: tuple[int, list[str], dict[str, int]] = (0, [], {})
    for row_no, _row in scan_window(path, sheet):
        for headers in header_candidates(path, sheet, row_no, kind):
            mapping = guess_mapping(headers, kind)
            if len(mapping) > len(best[2]):
                best = (row_no, headers, mapping)
    return best


def guess_mapping(headers: list[object], kind: str) -> dict[str, int]:
    if kind in REPORT_KINDS:
        item_headers = {_norm(name) for name in REPORT_ITEM_HEADERS}
        amount_headers = {_norm(name) for name in REPORT_AMOUNT_HEADERS}
        items = [i for i, value in enumerate(headers) if _norm(value) in item_headers]
        structural_items = [i for i in range(len(headers) - 1) if _norm(headers[i]) and _norm(headers[i + 1]) in amount_headers and _norm(headers[i]) not in amount_headers]
        items = sorted(set(items) | set(structural_items))
        if not items:
            return {}
        mapping = {"left_item": items[0]}
        sides = [("left", items[0], items[1] if len(items) > 1 else len(headers))]
        if kind == "balance_sheet" and len(items) > 1:
            mapping["right_item"] = items[1]
            sides.append(("right", items[1], len(headers)))
        for side, start, stop in sides:
            candidates = [i for i in range(start + 1, stop) if _norm(headers[i]) in amount_headers]
            for number, index in enumerate(candidates[:3], 1):
                mapping[f"{side}_amount_{number}"] = index
        return mapping
    mapping: dict[str, int] = {}
    fields = ("date", "fiscal_year", "period", "voucher_no", "voucher_type", "account_code", "account_name", "summary", "debit", "credit", "amount", "direction", "counterparty", "auxiliary", "currency", "original_amount", "maker") if kind == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "opening_balance", "closing_balance", "direction")
    for field in fields:
        aliases = FIELDS[field]
        for index, header in enumerate(headers):
            if _norm(header) in {_norm(a) for a in aliases}:
                mapping[field] = index
                break
    return mapping


@dataclass(frozen=True)
class ImportSpec:
    path: Path
    sheet: str
    header_row: int
    kind: str
    mapping: dict[str, int]
    data_start_row: int = 0
    data_end_row: int = 0
    excluded_rows: tuple[int, ...] = ()

    def with_extra_exclusions(self, extra: tuple[int, ...]) -> "ImportSpec":
        merged = tuple(sorted(set(self.excluded_rows) | {row for row in extra if row > self.header_row}))
        return replace(self, excluded_rows=merged)

    def validate(self) -> None:
        if self.kind not in ("entry", "balance", *REPORT_KINDS):
            raise ValueError("表类型无效")
        if self.header_row < 1:
            raise ValueError("表头行号必须大于 0")
        if self.kind in REPORT_KINDS:
            allowed = {f"{side}_{field}" for side in ("left", "right") for field in ("item", "amount_1", "amount_2", "amount_3")}
            if set(self.mapping) - allowed:
                raise ValueError("报表列映射包含未知字段")
            if "left_item" not in self.mapping or "left_amount_1" not in self.mapping:
                raise ValueError("请映射左侧项目与第一列金额；无法确认报表版式时不要导入")
            if "right_item" in self.mapping:
                if self.kind != "balance_sheet" or "right_amount_1" not in self.mapping:
                    raise ValueError("右侧报表区域必须有项目和第一列金额，且只适用于资产负债表")
            elif any(field in self.mapping for field in ("right_amount_1", "right_amount_2", "right_amount_3")):
                raise ValueError("右侧金额列必须同时映射右侧项目")
            if "left_amount_2" in self.mapping and "left_amount_1" not in self.mapping:
                raise ValueError("第二列金额不能单独映射")
            if "right_amount_2" in self.mapping and "right_amount_1" not in self.mapping:
                raise ValueError("右侧第二列金额不能单独映射")
            if "left_amount_3" in self.mapping and "left_amount_2" not in self.mapping:
                raise ValueError("第三列金额不能跳过第二列映射")
            if "right_amount_3" in self.mapping and "right_amount_2" not in self.mapping:
                raise ValueError("右侧第三列金额不能跳过第二列映射")
        else:
            if not ("account_code" in self.mapping or "account_name" in self.mapping):
                raise ValueError("至少映射科目编码或科目名称")
            needed = ("debit", "credit") if self.kind == "entry" else ("closing_debit", "closing_credit", "closing_balance")
            if not any(field in self.mapping for field in needed):
                # 序时账/明细账允许“单列金额 + 借贷方向”的画法（用友等导出格式）
                if not (self.kind == "entry" and "amount" in self.mapping and "direction" in self.mapping):
                    raise ValueError("至少映射一个借方或贷方金额列，或同时映射“金额”与“借贷方向”列")
        if self.kind == "entry" and "amount" in self.mapping and "direction" not in self.mapping:
            raise ValueError("单列金额必须同时映射借贷方向列，避免猜测借贷方向")
        if self.kind == "balance" and "closing_balance" in self.mapping and "direction" not in self.mapping:
            raise ValueError("单列期末余额必须同时映射余额方向，避免猜测借贷方向")
        if self.kind == "balance" and "opening_balance" in self.mapping and "direction" not in self.mapping:
            raise ValueError("单列期初余额必须同时映射余额方向，避免猜测借贷方向")
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError("同一列不能映射多个字段")
        if any(index < 0 for index in self.mapping.values()):
            raise ValueError("列序号无效")
        if self.data_start_row and self.data_start_row <= self.header_row:
            raise ValueError("数据起始行必须位于表头行之后")
        if self.data_end_row and self.data_end_row < (self.data_start_row or self.header_row + 1):
            raise ValueError("数据结束行不能早于数据起始行")
        if any(row <= self.header_row for row in self.excluded_rows):
            raise ValueError("排除行号必须位于表头之后")


# ---------------------------------------------------------------------------
# 行级分类器：每行都有去向


class _Pending(ValueError):
    """关键解析错误：导入前必须人工处理（调整列、排除该行或修正数据区域）。"""


def _looks_like_header(row: list[object], kind: str, width: int) -> int:
    """统计一行中命中的已知表头别名个数，用于识别重复表头/结构变化。"""
    aliases = set()
    if kind in REPORT_KINDS:
        aliases |= {_norm(name) for name in REPORT_ITEM_HEADERS} | {_norm(name) for name in REPORT_AMOUNT_HEADERS}
    else:
        fields = ("date", "voucher_no", "voucher_type", "account_code", "account_name", "summary", "debit", "credit", "amount", "direction") if kind == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "direction")
        for field in fields:
            aliases |= {_norm(alias) for alias in FIELDS[field]}
    return len({_norm(value) for value in row[:width] if value is not None} & aliases)


def _row_signature(row: list[object], width: int) -> tuple[str, ...]:
    return tuple(_norm(value) for value in row[:width])


def _direction_side(raw: object, label: str = "借贷方向") -> str:
    """解析方向单元格：借/贷（或 D/C/Dr/Cr）返回 debit/credit；无法识别时抛错，绝不猜测。"""
    text = text_value(raw)
    if text.startswith("借") or text.upper().startswith("D"):
        return "debit"
    if text.startswith("贷") or text.upper().startswith("C"):
        return "credit"
    raise ValueError(f"{label}无法识别：{text!r}")


def classify_rows(spec: ImportSpec, formulas: dict[tuple[int, int], str] | None = None) -> Iterator[RowDecision]:
    """逐行给出导入去向。``formulas`` 为公式坐标表：公式结果缺失时标记待处理而不是当空值。"""
    spec.validate()
    formulas = formulas or {}
    start_row = spec.data_start_row or spec.header_row + 1
    end_row = spec.data_end_row or 0
    excluded = set(spec.excluded_rows)
    width = max(spec.mapping.values(), default=0) + 1
    header_row_raw: list[object] = []
    header_signature_seen: tuple[str, ...] | None = None
    last_account_code = ""
    last_account_name = ""
    last_voucher = {field: "" for field in ("date", "fiscal_year", "period", "voucher_type", "voucher_no")}
    alien_since = 0
    last_subtotal_row = 0

    def mapped(row: list[object], field: str, row_no: int) -> object:
        index = spec.mapping[field]
        value = row[index] if index < len(row) else None
        if (value is None or str(value).strip() == "") and (row_no, index) in formulas:
            raise _Pending(f"公式结果缺失（文件未保存计算缓存），“{field}”所在列无法取值")
        return value

    for row_no, row in rows(spec.path, spec.sheet):
        if spec.kind in REPORT_KINDS and row_no < spec.header_row:
            title = "  ".join(text_value(value) for value in row if value is not None and str(value).strip())
            if title:
                yield RowDecision(row_no, DISPOSITION_IMPORT, "标题行", {
                    "left_item": title, "left_amount_1_cents": None, "left_amount_2_cents": None, "left_amount_3_cents": None,
                    "right_item": "", "right_amount_1_cents": None, "right_amount_2_cents": None, "right_amount_3_cents": None,
                    "raw_json": json.dumps([text_value(v) for v in row], ensure_ascii=False)})
            else:
                yield RowDecision(row_no, DISPOSITION_BLANK, "空行")
            continue
        if row_no < spec.header_row:
            yield RowDecision(row_no, DISPOSITION_TITLE, "表头之前的标题行")
            continue
        if row_no == spec.header_row:
            header_row_raw = row
            header_signature_seen = _row_signature(row, width)
            yield RowDecision(row_no, DISPOSITION_HEADER, "表头行")
            continue
        if row_no in excluded:
            yield RowDecision(row_no, DISPOSITION_EXCLUDED, "已由人工排除")
            continue
        if row_no < start_row or (end_row and row_no > end_row):
            yield RowDecision(row_no, DISPOSITION_RANGE, "位于设定的数据区域之外")
            continue
        if not any(value is not None and str(value).strip() for value in row):
            last_voucher = {field: "" for field in last_voucher}
            last_account_code = last_account_name = ""
            yield RowDecision(row_no, DISPOSITION_BLANK, "空行")
            continue
        if header_signature_seen is not None and _row_signature(row, width) == header_signature_seen:
            last_voucher = {field: "" for field in last_voucher}
            alien_since = 0
            yield RowDecision(row_no, DISPOSITION_HEADER, "重复表头（结构一致，连续分区自动合并）")
            continue
        if alien_since:
            yield RowDecision(row_no, DISPOSITION_PENDING, f"位于第 {alien_since} 行疑似表头（结构变化）之下；不同结构的分区不自动合并，请一并排除或拆分原表")
            continue
        first_text = next((text_value(value) for value in row if text_value(value)), "")
        if FOOTER.match(first_text):
            last_voucher = {field: "" for field in last_voucher}
            yield RowDecision(row_no, DISPOSITION_FOOTER, "页脚行")
            continue
        if spec.kind in REPORT_KINDS:
            try:
                values = {field: row[index] if index < len(row) else None for field, index in spec.mapping.items()}

                def report_amount(field: str) -> int | None:
                    value = values.get(field)
                    if value is None or str(value).strip() in ("", "—", "-", "–"):
                        return None
                    try:
                        return money(value)
                    except ValueError as exc:
                        raise _Pending(f"报表金额无法识别：{value}（{exc}）") from exc

                data = {
                    "left_item": text_value(values.get("left_item")),
                    "left_amount_1_cents": report_amount("left_amount_1"),
                    "left_amount_2_cents": report_amount("left_amount_2"),
                    "left_amount_3_cents": report_amount("left_amount_3"),
                    "right_item": text_value(values.get("right_item")),
                    "right_amount_1_cents": report_amount("right_amount_1"),
                    "right_amount_2_cents": report_amount("right_amount_2"),
                    "right_amount_3_cents": report_amount("right_amount_3"),
                    "raw_json": json.dumps([text_value(v) for v in row], ensure_ascii=False),
                }
            except _Pending as exc:
                yield RowDecision(row_no, DISPOSITION_PENDING, str(exc))
                continue
            if any(value is not None and value != "" for key, value in data.items() if key != "raw_json"):
                yield RowDecision(row_no, DISPOSITION_IMPORT, "报表数据行", data)
            else:
                yield RowDecision(row_no, DISPOSITION_BLANK, "空行")
            continue

        warnings: list[str] = []
        try:
            if _looks_like_header(row, spec.kind, width) >= 3 and header_signature_seen is not None:
                alien_since = row_no
                raise _Pending("疑似另一张表或结构变化的表头；该行下方的内容不会自动按当前列导入，请确认数据区域（可一并排除或拆分原表）")
            # 小计、维度子行的判断基于原始单元格，先于公式检查：合计行的公式缺缓存只影响对照，不应阻塞
            raw_code = text_value(row[spec.mapping["account_code"]]) if "account_code" in spec.mapping and spec.mapping["account_code"] < len(row) else ""
            raw_name = text_value(row[spec.mapping["account_name"]]) if "account_name" in spec.mapping and spec.mapping["account_name"] < len(row) else ""
            raw_summary = text_value(row[spec.mapping["summary"]]) if "summary" in spec.mapping and spec.mapping["summary"] < len(row) else ""
            # 用友等把总计打印成成对两行：“合计”只标在第一行，第二行除方向/金额外全空。
            # 紧邻合计行且没有任何标识（科目/摘要/日期/凭证）的裸行是合计的另一半，不作为分录导入。
            if last_subtotal_row and row_no == last_subtotal_row + 1 and not (raw_code or raw_name or raw_summary) and not any(
                text_value(row[spec.mapping[field]]) for field in ("date", "voucher_type", "voucher_no")
                if field in spec.mapping and spec.mapping[field] < len(row)
            ):
                last_subtotal_row = row_no
                yield RowDecision(row_no, DISPOSITION_SUBTOTAL, "合计行的续行（成对打印的合计另一半）")
                continue
            if spec.kind == "balance" and not (raw_code or raw_name):
                has_amount = False
                for amount_field in ("opening_debit", "opening_credit", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit", "opening_balance", "closing_balance"):
                    if amount_field in spec.mapping:
                        try:
                            has_amount = has_amount or money(mapped(row, amount_field, row_no)) != 0
                        except ValueError:
                            has_amount = True
                if not has_amount:
                    yield RowDecision(row_no, DISPOSITION_DIMENSION, "无科目且无金额的说明行")
                    continue
                if not last_account_code and not last_account_name:
                    raise _Pending("有金额但没有科目，且上方没有可继承的科目行")
                yield RowDecision(row_no, DISPOSITION_DIMENSION, "维度子行（金额与父科目重复，不计入科目合计）")
                continue
            if SUBTOTAL.match(raw_name) or SUBTOTAL.match(raw_summary) or raw_summary in ("期初余额", "上年结转"):
                last_voucher = {field: "" for field in last_voucher}
                last_subtotal_row = row_no
                subtotal_cents = None
                if spec.kind == "entry":
                    try:
                        subtotal_cents = (money(row[spec.mapping["debit"]]) if "debit" in spec.mapping and spec.mapping["debit"] < len(row) else None,
                                          money(row[spec.mapping["credit"]]) if "credit" in spec.mapping and spec.mapping["credit"] < len(row) else None)
                    except ValueError:
                        subtotal_cents = None
                yield RowDecision(row_no, DISPOSITION_SUBTOTAL, "小计/合计行", subtotal_cents=subtotal_cents)
                continue
            values = {field: mapped(row, field, row_no) for field in spec.mapping}
            code = text_value(values.get("account_code"))
            name = text_value(values.get("account_name"))
            summary = text_value(values.get("summary"))
            if code or name:
                last_account_code, last_account_name = code, name
            else:
                code, name = last_account_code, last_account_name
            if not code and not name:
                if spec.kind == "entry":
                    if "amount" in spec.mapping and "direction" in spec.mapping:
                        amount_cells = (mapped(row, "amount", row_no), None)
                    else:
                        amount_cells = (mapped(row, "debit", row_no) if "debit" in spec.mapping else None,
                                        mapped(row, "credit", row_no) if "credit" in spec.mapping else None)
                    if any(value is not None and str(value).strip() not in ("", "—", "-", "–") for value in amount_cells):
                        raise _Pending("有金额但没有科目，且上方没有可继承的科目行")
                yield RowDecision(row_no, DISPOSITION_ZERO, "无科目无金额行")
                continue
            values["account_code"] = code
            values["account_name"] = name
            if spec.kind == "entry":
                if "amount" in spec.mapping and "direction" in spec.mapping:
                    # 单列金额：方向“借”计入借方、“贷”计入贷方；零金额行不需要方向
                    single = money(mapped(row, "amount", row_no))
                    if single == 0:
                        yield RowDecision(row_no, DISPOSITION_ZERO, "借贷均为空或零的行")
                        continue
                    side = _direction_side(values.get("direction"))
                    debit, credit = (single, 0) if side == "debit" else (0, single)
                else:
                    debit, credit = money(mapped(row, "debit", row_no) if "debit" in spec.mapping else None), money(mapped(row, "credit", row_no) if "credit" in spec.mapping else None)
                if debit == 0 and credit == 0:
                    yield RowDecision(row_no, DISPOSITION_ZERO, "借贷均为空或零的行")
                    continue
                raw_date = text_value(values.get("date")) if "date" in spec.mapping else ""
                explicit_date = date_text(values.get("date")) if "date" in spec.mapping else ""
                if raw_date and explicit_date == raw_date and not DATE_NORMALIZED.fullmatch(explicit_date):
                    warnings.append(f"日期无法识别（{raw_date!r}），已按原文保留；请核对期间归属")
                explicit_year = year_text(mapped(row, "fiscal_year", row_no)) if "fiscal_year" in spec.mapping else ""
                explicit_period = period_text(mapped(row, "period", row_no)) if "period" in spec.mapping else ""
                explicit_type = text_value(values.get("voucher_type"))
                explicit_no = text_value(values.get("voucher_no"))
                derived_year, derived_period = period_from_date(explicit_date)
                if explicit_period and derived_period and explicit_period != derived_period:
                    raise _Pending(f"期间列（{explicit_period} 月）与日期（{explicit_date}，推算 {derived_period} 月）不一致")
                if explicit_year and derived_year and explicit_year != derived_year:
                    raise _Pending(f"年度列（{explicit_year}）与日期（{explicit_date}，推算 {derived_year}）不一致")
                year = explicit_year or derived_year or last_voucher["fiscal_year"]
                period = explicit_period or derived_period or last_voucher["period"]
                same_period = not (year and last_voucher["fiscal_year"] and year != last_voucher["fiscal_year"]) and not (period and last_voucher["period"] and period != last_voucher["period"])
                continuation = not explicit_date and not explicit_type and not explicit_no and same_period
                if continuation:
                    date = last_voucher["date"]
                    voucher_type = last_voucher["voucher_type"]
                    voucher_no = last_voucher["voucher_no"]
                    if not voucher_no and not date and any(field in spec.mapping for field in ("date", "voucher_type", "voucher_no")):
                        warnings.append("续行缺少可继承的凭证信息（可能位于重复表头或分页之后），请核对凭证归属")
                else:
                    same_voucher = explicit_no and explicit_no == last_voucher["voucher_no"] and (not explicit_type or explicit_type == last_voucher["voucher_type"]) and same_period
                    date = explicit_date or (last_voucher["date"] if same_voucher else "")
                    voucher_type = explicit_type or (last_voucher["voucher_type"] if same_voucher else "")
                    voucher_no = explicit_no
                last_voucher = {"date": date, "fiscal_year": year, "period": period, "voucher_type": voucher_type, "voucher_no": voucher_no}
                yield RowDecision(row_no, DISPOSITION_IMPORT, "分录行", {
                    "date": date,
                    "fiscal_year": year,
                    "period": period,
                    "voucher_no": voucher_no,
                    "voucher_type": voucher_type,
                    "account_code": code,
                    "account_name": name,
                    "summary": text_value(values.get("summary")),
                    "counterparty": text_value(values.get("counterparty")),
                    "auxiliary": text_value(values.get("auxiliary")),
                    "currency": text_value(values.get("currency")),
                    "original_amount": text_value(values.get("original_amount")),
                    "maker": text_value(values.get("maker")),
                    "debit_cents": debit,
                    "credit_cents": credit,
                    "raw_json": json.dumps([text_value(v) for v in row], ensure_ascii=False),
                }, warnings=tuple(warnings))
            else:
                amounts = {}
                for amount_field in ("opening_debit", "opening_credit", "period_debit", "period_credit", "year_debit", "year_credit", "closing_debit", "closing_credit"):
                    if amount_field in spec.mapping:
                        try:
                            amounts[amount_field + "_cents"] = money(mapped(row, amount_field, row_no))
                        except ValueError as exc:
                            raise _Pending(f"金额无法识别：{exc}") from exc
                    else:
                        amounts[amount_field + "_cents"] = 0
                direction = text_value(mapped(row, "direction", row_no)) if "direction" in spec.mapping else ""
                for period_name in ("opening", "closing"):
                    if period_name + "_balance" in spec.mapping:
                        single = money(mapped(row, period_name + "_balance", row_no))
                        if single:
                            base = _direction_side(direction, label="余额方向")
                            side = base if single > 0 else ("credit" if base == "debit" else "debit")
                            amounts[period_name + "_" + side + "_cents"] = abs(single)
                if not any(amounts.values()):
                    yield RowDecision(row_no, DISPOSITION_ZERO, "借贷余额均为空或零的行")
                    continue
                yield RowDecision(row_no, DISPOSITION_IMPORT, "余额行", {"account_code": code, "account_name": name, **amounts, "raw_json": json.dumps([text_value(v) for v in row], ensure_ascii=False)}, warnings=tuple(warnings))
        except _Pending as exc:
            yield RowDecision(row_no, DISPOSITION_PENDING, str(exc), warnings=tuple(warnings))
            continue
        except ValueError as exc:
            yield RowDecision(row_no, DISPOSITION_PENDING, str(exc), warnings=tuple(warnings))
            continue


def normalized_rows(spec: ImportSpec) -> Iterator[tuple[int, dict[str, object]]]:
    """严格视图：只产出可导入行；关键解析错误照旧抛出，保证写入前整体回滚。"""
    for decision in classify_rows(spec):
        if decision.disposition == DISPOSITION_PENDING:
            raise ValueError(f"第 {decision.row_no} 行：{decision.reason}")
        if decision.disposition == DISPOSITION_IMPORT:
            yield decision.row_no, decision.data


def is_cashflow_sheet(path: Path, sheet: str) -> bool:
    """Identify excluded cash-flow reports from sheet names, file names or titles."""
    name, filename = _norm(sheet), _norm(path.stem)
    if "现金流量" in name or "cashflow" in name:
        return True
    generic = not name or name.startswith(("sheet", "工作表")) or name == "csv"
    if generic and ("现金流量" in filename or "cashflow" in filename):
        return True
    iterator = rows(path, sheet)
    try:
        for row_no, values in iterator:
            if row_no > 8:
                break
            # Only inspect title rows; a ledger memo mentioning the report is data.
            populated = [_norm(value) for value in values if text_value(value)]
            if len(populated) <= 2 and any(value in ("现金流量表", "cashflowstatement", "statementofcashflows") for value in populated):
                return True
    finally:
        iterator.close()
    return False


def _template_match(path: Path, sheet: str, templates: list[dict]) -> ImportSpec | None:
    """已确认模板优先：按表头结构签名匹配（与文件名无关），映射仍需通过本地校验。"""
    if not templates:
        return None
    window_rows, _ranges = _window_maps(path, sheet)
    for row_no in sorted(window_rows):
        for headers in header_candidates(path, sheet, row_no, "balance"):
            signature = header_signature(headers)
            if not signature:
                continue
            for template in templates:
                if signature != template.get("header_signature"):
                    continue
                try:
                    spec = ImportSpec(path, sheet, row_no, template["kind"], dict(template["mapping"]))
                    if all(0 <= index < len(headers) for index in spec.mapping.values()):
                        spec.validate()
                        return spec
                except (ValueError, KeyError, TypeError):
                    continue
    return None


def classify_sheet(path: Path, sheet: str, templates: list[dict] | None = None) -> ImportSpec | None:
    """Classify only layouts with a validated header; uncertain sheets stay manual.
    匹配顺序：已确认模板 → 本地规则；模型建议与人工调整在界面层进行。"""
    name = _norm(sheet)
    filename = _norm(path.stem)
    if is_cashflow_sheet(path, sheet):
        return None
    from_template = _template_match(path, sheet, templates or [])
    if from_template is not None:
        return from_template
    hints = (("科目余额", "balance"), ("试算平衡", "balance"), ("资产负债", "balance_sheet"),
             ("利润表", "income_statement"), ("损益表", "income_statement"),
             ("序时账", "entry"), ("明细账", "entry"))
    stated = next((kind for word, kind in hints if word in name), None)
    if stated is None and (not name or name.startswith("sheet") or name.startswith("工作表") or name == "csv"):
        stated = next((kind for word, kind in hints if word in filename), None)
    candidates: list[tuple[int, ImportSpec]] = []
    kinds = (stated,) if stated else ("entry", "balance", "balance_sheet", "income_statement")
    for kind in kinds:
        header_row, headers, mapping = guess_header(path, sheet, kind)
        if not headers:
            continue
        spec = ImportSpec(path, sheet, header_row, kind, mapping)
        try:
            spec.validate()
        except ValueError:
            continue
        score = len(mapping)
        header_text = " ".join(headers)
        if kind == "balance_sheet" and ("期末余额" in header_text or "上年年末余额" in header_text):
            score += 3
        if kind == "income_statement" and ("本期金额" in header_text or "本年金额" in header_text):
            score += 3
        candidates.append((score, spec))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1]


def discover_sheets(paths: list[Path], templates: list[dict] | None = None) -> tuple[list[ImportSpec], list[str]]:
    detected = []
    skipped = []
    for path in paths:
        try:
            names = sheet_names(path)
        except Exception as exc:
            skipped.append(f"{path.name}：读取失败（{exc}）")
            continue
        for sheet in names:
            try:
                if is_cashflow_sheet(path, sheet):
                    continue
                spec = classify_sheet(path, sheet, templates)
            except Exception as exc:
                skipped.append(f"{path.name}/{sheet}：读取失败（{exc}）")
                continue
            if spec is None:
                reason = "类型或表头不能可靠自动确认，请在导入列表中设置列"
                skipped.append(f"{path.name}/{sheet}：{reason}")
            else:
                detected.append(spec)
    return detected, skipped
