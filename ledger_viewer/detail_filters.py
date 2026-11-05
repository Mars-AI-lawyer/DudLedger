"""Typed conditions for the detail ledger and its CSV export."""

from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import NamedTuple

from .importer import date_text, decimal_amount


TEXT_COLUMNS = {
    "summary": "e.summary",
    "voucher": "(e.voucher_type || e.voucher_no)",
    "code": "a.code",
    "name": "a.name",
    "counterparty": "e.counterparty",
    "aux": "e.auxiliary",
    "currency": "e.currency",
    "maker": "e.maker",
}
CENTS_COLUMNS = {"debit": "e.debit_cents", "credit": "e.credit_cents"}
FILTER_COLUMNS = frozenset((*TEXT_COLUMNS, *CENTS_COLUMNS, "date", "original"))
COMPARISONS = {"gt": ">", "ge": ">=", "eq": "=", "le": "<=", "lt": "<"}
VALID_DATE_SQL = "e.entry_date >= '0001-01-01' AND date(e.entry_date, '+0 days') = e.entry_date"
SQLITE_MIN_INT, SQLITE_MAX_INT = -(2 ** 63), 2 ** 63 - 1


class EntryCondition(NamedTuple):
    column: str
    operator: str
    values: tuple[str, ...]


def make_condition(column: str, operator: str, *values: str) -> EntryCondition:
    if column not in FILTER_COLUMNS:
        raise ValueError("请选择可筛选的列")
    if column in TEXT_COLUMNS:
        if operator not in ("contains", "excludes", "eq") or len(values) != 1 or not values[0].strip():
            raise ValueError("请输入要匹配的文字")
        return EntryCondition(column, operator, (values[0].strip(),))
    if column == "date" and operator == "months":
        if not values:
            raise ValueError("请至少选择一个自然月")
        try:
            months = {date.fromisoformat(f"{value}-01").isoformat()[:7] for value in values}
        except ValueError as exc:
            raise ValueError("请选择有效的自然月") from exc
        return EntryCondition(column, operator, tuple(sorted(months)))
    if operator not in (*COMPARISONS, "between") or len(values) != (2 if operator == "between" else 1):
        raise ValueError("请选择比较方式并填写条件")
    if column == "date":
        try:
            normalized = tuple(date.fromisoformat(date_text(value)).isoformat() for value in values)
        except ValueError as exc:
            raise ValueError("请输入有效日期，例如 2025-03-31") from exc
        ordered = len(normalized) == 1 or normalized[0] <= normalized[1]
    else:
        if any("e" in value.lower() for value in values):
            raise ValueError("请输入十进制金额，例如 1,000.50")
        numbers = tuple(decimal_amount(value) for value in values)
        normalized = tuple(str(number) for number in numbers)
        ordered = len(numbers) == 1 or numbers[0] <= numbers[1]
    if not ordered:
        raise ValueError("起始值须小于或等于结束值")
    return EntryCondition(column, operator, normalized)


def compare_original_amount(raw: str, threshold: str) -> int | None:
    try:
        value = decimal_amount(raw)
    except ValueError:
        return None
    limit = Decimal(threshold)
    return (value > limit) - (value < limit)


def _cents_comparison(expression: str, operator: str, value: str) -> tuple[str, list]:
    threshold = Decimal(value)
    with localcontext() as context:
        context.prec = max(context.prec, len(threshold.as_tuple().digits) + 2)
        scaled = threshold * 100
    floor = int(scaled.to_integral_value(rounding=ROUND_FLOOR))
    ceiling = int(scaled.to_integral_value(rounding=ROUND_CEILING))
    if operator == "eq" and floor != ceiling:
        return "0", []
    bounds = {"gt": ("ge", floor + 1), "ge": ("ge", ceiling),
              "eq": ("eq", floor), "le": ("le", floor), "lt": ("le", ceiling - 1)}
    comparison, cents = bounds[operator]
    if cents < SQLITE_MIN_INT:
        return ("1" if comparison == "ge" else "0"), []
    if cents > SQLITE_MAX_INT:
        return ("1" if comparison == "le" else "0"), []
    return f"{expression} {COMPARISONS[comparison]} ?", [cents]


def condition_clause(condition: EntryCondition) -> tuple[str, list]:
    column, operator, values = condition
    if column in TEXT_COLUMNS:
        expression = f"lower({TEXT_COLUMNS[column]})"
        if operator == "eq":
            return f"{expression} = lower(?)", list(values)
        comparison = {"contains": "> 0", "excludes": "= 0"}[operator]
        return f"instr({expression}, lower(?)) {comparison}", list(values)
    if column == "date" and operator == "months":
        placeholders = ",".join("?" for _ in values)
        return f"{VALID_DATE_SQL} AND substr(e.entry_date, 1, 7) IN ({placeholders})", list(values)

    comparisons = (("ge", values[0]), ("le", values[1])) if operator == "between" else ((operator, values[0]),)
    args = []
    if column in CENTS_COLUMNS:
        expression = CENTS_COLUMNS[column]
        clauses = [f"{expression} <> 0"]
        for comparison, value in comparisons:
            clause, params = _cents_comparison(expression, comparison, value)
            clauses.append(clause)
            args.extend(params)
    elif column == "date":
        clauses = [VALID_DATE_SQL]
        for comparison, value in comparisons:
            clauses.append(f"e.entry_date {COMPARISONS[comparison]} ?")
            args.append(value)
    elif column == "original":
        clauses = []
        for comparison, value in comparisons:
            clauses.append(f"compare_original_amount(e.original_amount, ?) {COMPARISONS[comparison]} 0")
            args.append(value)
    else:
        raise ValueError("请选择可筛选的列")
    return " AND ".join(clauses), args
