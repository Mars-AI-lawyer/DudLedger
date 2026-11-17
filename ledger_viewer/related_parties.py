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


def build_party_index(parties: list) -> dict[str, list[tuple[int, str]]]:
    """Prepare names once for a batch of rows, preserving ambiguous matches."""
    index: dict[str, list[tuple[int, str]]] = {}
    for number, party in enumerate(parties):
        try:
            aliases = json.loads(party["aliases_json"])
        except (TypeError, ValueError):
            aliases = []
        names = {normalized_name(party["name"]), *(normalized_name(alias) for alias in aliases if isinstance(alias, str))}
        for name in names:
            index.setdefault(name, []).append((number, party["name"]))
    return index


def indexed_party_match(row, index: dict[str, list[tuple[int, str]]]) -> str:
    matched = {number: name for token in party_tokens(row["counterparty"]) | party_tokens(row["auxiliary"])
               for number, name in index.get(token, ())}
    if len(matched) > 1:
        return "多方匹配·待核"
    return next(iter(matched.values()), "")


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


def review_party_context(parties: list) -> dict:
    """Keep each identity, alias and user relationship together; exclude private notes."""
    records = []
    for party in parties:
        names = party_names([party])
        if not names:
            continue
        records.append({'name': names[0], 'aliases': names[1:], 'roles': party_roles(party),
                        'relation': (party['relation'] or '').strip() if 'relation' in party.keys() else ''})
    return {'parties': records}


def parse_party_lines(text: str) -> list[tuple[str, list[str]]]:
    """One party per line: “名称” or “名称｜股东,董事”; invalid roles are dropped."""
    return [(name, roles) for name, roles, _relation in parse_party_records(text)]


def parse_party_records(text: str) -> list[tuple[str, list[str], str]]:
    """Parse editable name | roles | relationship records without losing relationships."""
    entries = []
    seen: set[str] = set()
    for line in text.splitlines():
        value = unicodedata.normalize("NFKC", line or "").strip()
        if not value:
            continue
        parts = re.split(r'[|｜]', value, maxsplit=2)
        name = parts[0].strip()
        if not name or normalized_name(name) in seen:
            continue
        seen.add(normalized_name(name))
        entries.append((name, parse_roles(parts[1]) if len(parts) > 1 else [], parts[2].strip() if len(parts) > 2 else ''))
    return entries


_HEADERS = {"名称", "关联方", "关联方名称", "关联方清单", "公司", "公司名称", "尽调主体", "name", "party"}

def party_entries_from_file(path) -> list[tuple[str, list[str]]]:
    """Import parties from a spreadsheet or text file.

    The first non-empty cell is the name; when a second non-empty cell parses
    as known role words (e.g. “股东,董事”), it becomes the party's roles.
    """
    return [(name, roles) for name, roles, _relation in party_records_from_file(path)]


def party_records_from_file(path) -> list[tuple[str, list[str], str]]:
    """Read named columns, retaining empty role cells and explicit relationships."""
    path = Path(path)
    if path.suffix.lower() == ".txt":
        return parse_party_records(path.read_text(encoding="utf-8-sig", errors="replace"))
    from .importer import rows, sheet_names

    entries = []
    seen: set[str] = set()
    sheet = "CSV" if path.suffix.lower() == ".csv" else sheet_names(path)[0]
    columns = None
    first = True
    for _row_no, values in rows(path, sheet):
        cells = [unicodedata.normalize("NFKC", str(value)).strip() if value is not None else '' for value in values]
        if not any(cells):
            continue
        if first:
            first = False
            name_col = next((i for i, cell in enumerate(cells) if cell.casefold() in _HEADERS), None)
            if name_col is not None:
                columns = {'name': name_col,
                           'roles': next((i for i, cell in enumerate(cells) if cell in ('身份', '身份类型', '角色', 'roles')), None),
                           'relation': next((i for i, cell in enumerate(cells) if cell in ('与目标公司关系', '关系', '关联关系', 'relation')), None)}
                continue
        def cell_at(index):
            return cells[index] if index is not None and index < len(cells) else ''
        if columns is None:
            name_index = next(i for i, cell in enumerate(cells) if cell)
            name = cells[name_index]
            roles = parse_roles(cell_at(name_index + 1))
            relation = cell_at(name_index + 2)
        else:
            name = cell_at(columns['name'])
            roles = parse_roles(cell_at(columns['roles']))
            relation = cell_at(columns['relation'])
        if not name:
            continue
        if normalized_name(name) in seen:
            continue
        seen.add(normalized_name(name))
        entries.append((name, roles, relation))
    return entries

def party_names_from_file(path) -> list[str]:
    """Backward-compatible flat name import."""
    return [name for name, _roles in party_entries_from_file(path)]
