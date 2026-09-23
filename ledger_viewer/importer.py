"""Workbook inspection and normalization. Original files are never changed."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterator

from openpyxl import load_workbook


FIELDS = {
    "date": ("日期", "凭证日期", "业务日期", "记账日期", "date"),
    "fiscal_year": ("会计年度", "账簿年度", "年度", "年", "fiscal year"),
    "period": ("期间", "会计期间", "月份", "月", "period"),
    "voucher_no": ("凭证号", "凭证编号", "凭证号码", "凭证字号", "voucher no", "voucher number"),
    "voucher_type": ("凭证字", "凭证类型", "凭证类别", "字", "voucher type"),
    "account_code": ("科目编码", "科目代码", "会计科目编码", "科目编号", "account code"),
    "account_name": ("科目名称", "科目全名", "会计科目", "科目", "account name"),
    "summary": ("摘要", "业务摘要", "说明", "description", "memo"),
    "debit": ("借方金额", "借方发生额", "本期借方", "借方", "debit"),
    "credit": ("贷方金额", "贷方发生额", "本期贷方", "贷方", "credit"),
    "counterparty": ("往来单位", "对方单位", "客商", "客户", "供应商", "counterparty"),
    "auxiliary": ("辅助核算", "核算维度", "核算项目", "辅助项", "项目", "部门", "auxiliary"),
    "currency": ("币别", "币种", "货币", "currency"),
    "original_amount": ("原币金额", "外币金额", "原币", "original amount"),
    "maker": ("制单", "制单人", "录入人", "prepared by"),
    "opening_debit": ("期初借方", "期初借方余额", "年初借方", "opening debit"),
    "opening_credit": ("期初贷方", "期初贷方余额", "年初贷方", "opening credit"),
    "period_debit": ("本期借方发生额", "借方本期发生额", "本期借方", "借方发生额", "period debit"),
    "period_credit": ("本期贷方发生额", "贷方本期发生额", "本期贷方", "贷方发生额", "period credit"),
    "closing_debit": ("期末借方", "期末借方余额", "期末借方金额", "closing debit"),
    "closing_credit": ("期末贷方", "期末贷方余额", "期末贷方金额", "closing credit"),
    "opening_balance": ("期初余额", "年初余额", "opening balance"),
    "closing_balance": ("期末余额", "closing balance"),
    "direction": ("余额方向", "借贷方向", "方向", "balance direction"),
}

SUBTOTAL = re.compile(r"^(?:本月|本年|本期|全年|当月)?(?:合计|累计|小计|总计)$")


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


def sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() == ".csv":
        return ["CSV"]
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("只支持 .xlsx、.xlsm、.csv；旧版 .xls 请先另存为 .xlsx")
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def rows(path: Path, sheet: str) -> Iterator[tuple[int, list[object]]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader, 1):
                yield index, list(row)
        return
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in book.sheetnames:
            raise ValueError(f"工作表不存在：{sheet}")
        for index, row in enumerate(book[sheet].iter_rows(values_only=True), 1):
            yield index, list(row)
    finally:
        book.close()


def guess_mapping(headers: list[object], kind: str) -> dict[str, int]:
    mapping: dict[str, int] = {}
    fields = ("date", "fiscal_year", "period", "voucher_no", "voucher_type", "account_code", "account_name", "summary", "debit", "credit", "counterparty", "auxiliary", "currency", "original_amount", "maker") if kind == "entry" else ("account_code", "account_name", "opening_debit", "opening_credit", "opening_balance", "period_debit", "period_credit", "closing_debit", "closing_credit", "closing_balance", "direction")
    for field in fields:
        aliases = FIELDS[field]
        for index, header in enumerate(headers):
            if _norm(header) in {_norm(a) for a in aliases}:
                mapping[field] = index
                break
    return mapping


def guess_header(path: Path, sheet: str, kind: str) -> tuple[int, list[str], dict[str, int]]:
    best = (0, [], {})
    for row_no, row in rows(path, sheet):
        mapping = guess_mapping(row, kind)
        if len(mapping) > len(best[2]):
            best = (row_no, [text_value(v) for v in row], mapping)
        if row_no >= 20:
            break
    return best


@dataclass(frozen=True)
class ImportSpec:
    path: Path
    sheet: str
    header_row: int
    kind: str
    mapping: dict[str, int]

    def validate(self) -> None:
        if self.kind not in ("entry", "balance"):
            raise ValueError("表类型必须是明细或余额")
        if self.header_row < 1:
            raise ValueError("表头行号必须大于 0")
        if not ("account_code" in self.mapping or "account_name" in self.mapping):
            raise ValueError("至少映射科目编码或科目名称")
        needed = ("debit", "credit") if self.kind == "entry" else ("closing_debit", "closing_credit", "closing_balance")
        if not any(field in self.mapping for field in needed):
            raise ValueError("至少映射一个借方或贷方金额列")
        if self.kind == "balance" and "closing_balance" in self.mapping and "direction" not in self.mapping:
            raise ValueError("单列期末余额必须同时映射余额方向，避免猜测借贷方向")
        if self.kind == "balance" and "opening_balance" in self.mapping and "direction" not in self.mapping:
            raise ValueError("单列期初余额必须同时映射余额方向，避免猜测借贷方向")
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError("同一列不能映射多个字段")
        if any(index < 0 for index in self.mapping.values()):
            raise ValueError("列序号无效")


def normalized_rows(spec: ImportSpec) -> Iterator[tuple[int, dict[str, object]]]:
    spec.validate()
    last_account_code = ""
    last_account_name = ""
    last_voucher = {field: "" for field in ("date", "fiscal_year", "period", "voucher_type", "voucher_no")}
    for row_no, row in rows(spec.path, spec.sheet):
        if row_no <= spec.header_row:
            continue
        if not any(value is not None and str(value).strip() for value in row):
            last_voucher = {field: "" for field in last_voucher}
            last_account_code = last_account_name = ""
            continue
        values = {field: row[index] if index < len(row) else None for field, index in spec.mapping.items()}
        code = text_value(values.get("account_code"))
        name = text_value(values.get("account_name"))
        summary = text_value(values.get("summary"))
        if SUBTOTAL.match(name) or SUBTOTAL.match(summary) or summary in ("期初余额", "上年结转"):
            last_voucher = {field: "" for field in last_voucher}
            continue
        if code or name:
            last_account_code, last_account_name = code, name
        else:
            code, name = last_account_code, last_account_name
        if not code and not name:
            continue
        values["account_code"] = code
        values["account_name"] = name
        if spec.kind == "entry":
            debit, credit = money(values.get("debit")), money(values.get("credit"))
            if debit == 0 and credit == 0:
                continue
            explicit_date = date_text(values.get("date"))
            explicit_year = year_text(values.get("fiscal_year"))
            explicit_period = period_text(values.get("period"))
            explicit_type = text_value(values.get("voucher_type"))
            explicit_no = text_value(values.get("voucher_no"))
            derived_year, derived_period = period_from_date(explicit_date)
            year = explicit_year or derived_year or last_voucher["fiscal_year"]
            period = explicit_period or derived_period or last_voucher["period"]
            same_period = not (year and last_voucher["fiscal_year"] and year != last_voucher["fiscal_year"]) and not (period and last_voucher["period"] and period != last_voucher["period"])
            continuation = not explicit_date and not explicit_type and not explicit_no and same_period
            if continuation:
                date = last_voucher["date"]
                voucher_type = last_voucher["voucher_type"]
                voucher_no = last_voucher["voucher_no"]
            else:
                same_voucher = explicit_no and explicit_no == last_voucher["voucher_no"] and (not explicit_type or explicit_type == last_voucher["voucher_type"]) and same_period
                date = explicit_date or (last_voucher["date"] if same_voucher else "")
                voucher_type = explicit_type or (last_voucher["voucher_type"] if same_voucher else "")
                voucher_no = explicit_no
            last_voucher = {"date": date, "fiscal_year": year, "period": period, "voucher_type": voucher_type, "voucher_no": voucher_no}
            yield row_no, {
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
            }
        else:
            amounts = {field + "_cents": money(values.get(field)) for field in ("opening_debit", "opening_credit", "period_debit", "period_credit", "closing_debit", "closing_credit")}
            direction = text_value(values.get("direction"))
            for period in ("opening", "closing"):
                single = money(values.get(period + "_balance"))
                if single:
                    if direction.startswith("借") or direction.lower().startswith("d"):
                        side = "debit" if single > 0 else "credit"
                    elif direction.startswith("贷") or direction.lower().startswith("c"):
                        side = "credit" if single > 0 else "debit"
                    else:
                        raise ValueError(f"第 {row_no} 行余额方向无法识别：{direction!r}")
                    amounts[period + "_" + side + "_cents"] = abs(single)
            if not any(amounts.values()):
                continue
            yield row_no, {"account_code": code, "account_name": name, **amounts, "raw_json": json.dumps([text_value(v) for v in row], ensure_ascii=False)}
