"""Exact matching against a user-maintained related-party list."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def normalized_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


# 尽调主体身份类型；一个主体可以具有多个身份。
PARTY_ROLES = ("股东", "实际控制人", "创始人", "董事", "监事", "高级管理人员", "核心员工", "前股东",
               "关联企业", "子公司", "现有投资人", "主要客户", "主要供应商", "贷款银行", "其他")
UNSPECIFIED_ROLE = "未注明身份"


def parse_roles(text) -> list[str]:
    """逗号／顿号分隔的身份列表；未知身份直接丢弃，不做猜测。"""
    if isinstance(text, str):
        tokens = re.split(r"[,，、;；/|｜]", text)
    elif isinstance(text, (list, tuple)):
        tokens = list(text)
    else:
        return []
    roles: list[str] = []
    for token in tokens:
        token = unicodedata.normalize("NFKC", str(token or "")).strip()
        if token in PARTY_ROLES and token not in roles:
            roles.append(token)
    return roles


def party_roles(party) -> list[str]:
    try:
        raw = json.loads(party["roles_json"]) if "roles_json" in party.keys() else []
    except (TypeError, ValueError, AttributeError):
        raw = []
    if not isinstance(raw, list):
        return []
    return [role for role in raw if isinstance(role, str) and role in PARTY_ROLES]


def party_tokens(value: str) -> set[str]:
    if not value:
        return set()
    result = {normalized_name(value)}
    prefix, separator, rest = value.partition("/")
    if separator and re.fullmatch(r"[A-Za-z]{2,}\d{2,}", prefix.strip()):
        result.add(normalized_name(rest))
    return result


def related_party_matches(row, parties: list) -> list:
    """Exact-name matches (name + aliases) against counterparty and auxiliary fields."""
    tokens = party_tokens(row["counterparty"]) | party_tokens(row["auxiliary"])
    if not tokens:
        return []
    matched = []
    for party in parties:
        try:
            aliases = json.loads(party["aliases_json"])
        except (TypeError, ValueError):
            aliases = []
        names = {normalized_name(party["name"]), *(normalized_name(alias) for alias in aliases if isinstance(alias, str))}
        if tokens & names:
            matched.append(party)
    return matched


def related_party_match(row, parties: list) -> str:
    matched = related_party_matches(row, parties)
    if len(matched) > 1:
        return "多方匹配·待核"
    return matched[0]["name"] if matched else ""


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


def role_grouped_names(parties: list) -> dict[str, list[str]]:
    """Group names by due-diligence role for compact cloud payloads.

    One party with several roles appears under each role; parties without any
    role fall into a shared bucket so their names are still sent.
    """
    groups: dict[str, list[str]] = {}
    seen_by_role: dict[str, set[str]] = {}
    for party in parties:
        try:
            aliases = json.loads(party["aliases_json"])
        except (TypeError, ValueError):
            aliases = []
        candidates = [party["name"], *(a for a in aliases if isinstance(a, str))]
        roles = party_roles(party) or [UNSPECIFIED_ROLE]
        for role in roles:
            bucket = groups.setdefault(role, [])
            seen = seen_by_role.setdefault(role, set())
            for value in candidates:
                value = unicodedata.normalize("NFKC", value or "").strip()
                if not value or normalized_name(value) in seen:
                    continue
                seen.add(normalized_name(value))
                bucket.append(value)
    return groups


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


def parse_party_lines(text: str) -> list[tuple[str, list[str]]]:
    """One party per line: “名称” or “名称｜股东,董事”; invalid roles are dropped."""
    entries: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        value = unicodedata.normalize("NFKC", line or "").strip()
        if not value:
            continue
        name, separator, role_text = value.partition("｜")
        if not separator:
            name, separator, role_text = value.partition("|")
        name = name.strip()
        if not name or normalized_name(name) in seen:
            continue
        seen.add(normalized_name(name))
        entries.append((name, parse_roles(role_text) if separator else []))
    return entries


_HEADERS = {"名称", "关联方", "关联方名称", "关联方清单", "公司", "公司名称", "尽调主体", "name", "party"}

def party_entries_from_file(path) -> list[tuple[str, list[str]]]:
    """Import parties from a spreadsheet or text file.

    The first non-empty cell is the name; when a second non-empty cell parses
    as known role words (e.g. “股东,董事”), it becomes the party's roles.
    """
    path = Path(path)
    if path.suffix.lower() == ".txt":
        return parse_party_lines(path.read_text(encoding="utf-8-sig", errors="replace"))
    from .importer import rows, sheet_names

    entries: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    sheet = "CSV" if path.suffix.lower() == ".csv" else sheet_names(path)[0]
    for _row_no, values in rows(path, sheet):
        cells = [unicodedata.normalize("NFKC", str(value)).strip() for value in values
                 if value is not None and str(value).strip()]
        if not cells:
            continue
        name = cells[0]
        roles = parse_roles(cells[1]) if len(cells) > 1 else []
        if normalized_name(name) in seen:
            continue
        seen.add(normalized_name(name))
        entries.append((name, roles))
    if entries and entries[0][0].casefold() in _HEADERS:
        entries = entries[1:]
    return entries

def party_names_from_file(path) -> list[str]:
    """Backward-compatible flat name import."""
    return [name for name, _roles in party_entries_from_file(path)]
