"""Exact matching against a user-maintained related-party list."""

from __future__ import annotations

import json
import re
import unicodedata


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
