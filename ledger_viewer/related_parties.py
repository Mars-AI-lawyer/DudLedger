"""Exact matching against a user-maintained related-party list."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def normalized_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def party_tokens(value: str) -> set[str]:
    if not value:
        return set()
    result = {normalized_name(value)}
    prefix, separator, rest = value.partition("/")
    if separator and re.fullmatch(r"[A-Za-z]{2,}\d{2,}", prefix.strip()):
        result.add(normalized_name(rest))
    return result


def related_party_match(row, parties: list) -> str:
    tokens = party_tokens(row["counterparty"]) | party_tokens(row["auxiliary"])
    if not tokens:
        return ""
    matched = []
    for party in parties:
        try:
            aliases = json.loads(party["aliases_json"])
        except (TypeError, ValueError):
            aliases = []
        names = {normalized_name(party["name"]), *(normalized_name(alias) for alias in aliases if isinstance(alias, str))}
        if tokens & names:
            matched.append(party["name"])
    if len(matched) > 1:
        return "多方匹配·待核"
    return matched[0] if matched else ""


def party_names(parties: list) -> list[str]:
    """Flatten stored parties into a deduplicated name list for cloud requests."""
    names: list[str] = []
    seen: set[str] = set()
    for party in parties:
        try:
            aliases = json.loads(party["aliases_json"])
        except (TypeError, ValueError):
            aliases = []
        candidates = [party["name"], *(a for a in aliases if isinstance(a, str))]
        for value in candidates:
            value = unicodedata.normalize("NFKC", value or "").strip()
            if not value or normalized_name(value) in seen:
                continue
            seen.add(normalized_name(value))
            names.append(value)
    return names


def parse_party_names(text: str) -> list[str]:
    """One name per line; blank lines and duplicates (after normalization) are dropped."""
    names: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        value = unicodedata.normalize("NFKC", line or "").strip()
        if not value or normalized_name(value) in seen:
            continue
        seen.add(normalized_name(value))
        names.append(value)
    return names


_HEADERS = {"名称", "关联方", "关联方名称", "关联方清单", "公司", "公司名称", "name", "party"}

def party_names_from_file(path) -> list[str]:
    """Import names from a spreadsheet or text file: the first non-empty cell per row."""
    path = Path(path)
    if path.suffix.lower() == ".txt":
        return parse_party_names(path.read_text(encoding="utf-8-sig", errors="replace"))
    from .importer import rows, sheet_names

    names: list[str] = []
    seen: set[str] = set()
    sheet = "CSV" if path.suffix.lower() == ".csv" else sheet_names(path)[0]
    for _row_no, values in rows(path, sheet):
        cell = next((unicodedata.normalize("NFKC", str(value)).strip() for value in values
                     if value is not None and str(value).strip()), "")
        if not cell or normalized_name(cell) in seen:
            continue
        seen.add(normalized_name(cell))
        names.append(cell)
    if names and names[0].casefold() in _HEADERS:
        names = names[1:]
    return names
