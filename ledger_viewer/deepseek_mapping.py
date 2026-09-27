"""Optional DeepSeek suggestions for spreadsheet columns. No import happens here."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import keyring

from .importer import ImportSpec
from .jev_mapping import FIELD_DESCRIPTIONS, KIND_DESCRIPTIONS, KIND_FIELDS, MappingSuggestion, critical_warnings

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
KEYRING_SERVICE = "DudLedger DeepSeek"
KEYRING_ACCOUNT = "deepseek_api_key"
TIMEOUT = 40.0

_SESSION_KEY = None
_MEMORY_ONLY = False


def _secure_backend() -> bool:
    # Never silently accept a plaintext/file backend selected by third-party plugins.
    try:
        module = type(keyring.get_keyring()).__module__
        return module in ('keyring.backends.macOS', 'keyring.backends.Windows', 'keyring.backends.SecretService', 'keyring.backends.kwallet')
    except Exception:
        return False


def get_api_key() -> str:
    global _MEMORY_ONLY
    if _SESSION_KEY is not None:
        return _SESSION_KEY
    try:
        if _secure_backend():
            value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ''
            _MEMORY_ONLY = False
            return value
    except Exception:
        pass
    _MEMORY_ONLY = True
    return ''


def key_memory_only() -> bool:
    return _MEMORY_ONLY


def save_api_key(value: str) -> None:
    global _SESSION_KEY, _MEMORY_ONLY
    value = value.strip()
    if not value or not value.isascii() or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError('API Key 不能为空，且只能包含不带空格的 ASCII 字符')
    try:
        if not _secure_backend():
            raise RuntimeError('No secure backend')
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
    except Exception:
        _SESSION_KEY, _MEMORY_ONLY = value, True
    else:
        _SESSION_KEY, _MEMORY_ONLY = None, False


def delete_api_key() -> None:
    global _SESSION_KEY, _MEMORY_ONLY
    try:
        if _secure_backend():
            if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT):
                keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        elif _SESSION_KEY is None or not _MEMORY_ONLY:
            raise RuntimeError('No secure backend')
    except Exception:
        _SESSION_KEY = ''
        raise ValueError('本次运行已停用密钥，但无法确认系统凭据库中的旧密钥已删除；请在系统凭据管理中检查。') from None
    _SESSION_KEY, _MEMORY_ONLY = '', False


_FIELD_HINT = "\n".join(f"- {name}：{FIELD_DESCRIPTIONS[name]}" for fields in KIND_FIELDS.values() for name in fields)
_KIND_HINT = "\n".join(f"- {name}：{description}" for name, description in KIND_DESCRIPTIONS.items())


def _messages(preview: dict) -> list[dict]:
    rows = json.dumps(preview, ensure_ascii=False)
    user = (
        f"下面是一个工作表的前 {len(preview['rows'])} 行预览，每项的 row 是电子表格行号，cells 是该行单元格文本（可能被截断）：\n{rows}\n\n"
        "请判断这张表的类型、表头行和每列的含义，严格只输出一个 JSON 对象：\n"
        '{"kind": "entry|balance|balance_sheet|income_statement|unknown", "header_row": 行号整数, '
        '"mapping": {"字段名": 列号整数}, "warnings": ["需要人工核对之处"]}\n\n'
        f"表类型定义：\n{_KIND_HINT}\n\n"
        f"可用字段（mapping 的键只能是所选表类型对应的字段名，列号从 1 开始；没有对应列的字段不要写入 mapping）：\n{_FIELD_HINT}\n\n"
        "规则：\n"
        "- header_row 必须是预览中真实存在的行号；两层表头时选借贷等子标题所在的最后一行，不要选公司名称或报表标题行。\n"
        "- 无法可靠判断时 kind 选 unknown 且 mapping 留空；宁可空置，不要猜测。\n"
        "- 借方/贷方金额列不能选成余额列；本年累计不能选成本期发生。\n"
        "- warnings 用中文列出不确定之处，例如表头不规则、借贷列易混淆等。"
    )
    return [{"role": "system", "content": "你是中国会计电子表格识别助手。只输出一个 JSON 对象，不要输出任何其他文字。"},
            {"role": "user", "content": user}]


def _ca_context() -> ssl.SSLContext:
    """Verify TLS normally. python.org builds may ship without any CA bundle, in
    which case fall back to the operating system store — the same bundle curl uses."""
    context = ssl.create_default_context()
    if context.get_ca_certs():
        return context
    for candidate in ("/etc/ssl/cert.pem", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/certs/ca-certificates.crt"):
        path = Path(candidate)
        if path.exists():
            loaded = ssl.create_default_context(cafile=str(path))
            if loaded.get_ca_certs():
                return loaded
    return context


def _chat(api_key: str, payload: dict) -> str:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_ca_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise ValueError(f"DeepSeek 接口返回 {exc.code}：{detail or exc.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"无法连接 DeepSeek：{exc}") from None
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("DeepSeek 返回内容无法解析") from None


def _json_answer(content: str) -> dict:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("DeepSeek 未返回 JSON 结果")
    try:
        answer = json.loads(content[start:end + 1])
    except ValueError:
        raise ValueError("DeepSeek 返回的 JSON 无法解析") from None
    if not isinstance(answer, dict):
        raise ValueError("DeepSeek 返回的 JSON 结构不符合要求")
    return answer


def suggest_mapping(preview: dict, api_key: str, chat=_chat) -> MappingSuggestion:
    """One DeepSeek request returns kind, header row and column mapping; code validates every value."""
    numbers = [int(item["row"]) for item in preview["rows"]]
    if not numbers:
        raise ValueError("没有可分析的预览行")
    payload = {
        "model": MODEL,
        "messages": _messages(preview),
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
        # The user chose a no-thinking quick model; keep latency and cost low.
        "thinking": {"type": "disabled"},
    }
    answer = _json_answer(chat(api_key, payload))
    kind = answer.get("kind")
    if kind not in KIND_FIELDS:
        raise ValueError("DeepSeek 无法可靠判断表类型，请使用手工映射")
    try:
        header_row = int(answer.get("header_row"))
    except (TypeError, ValueError):
        raise ValueError("DeepSeek 未返回有效的表头行号") from None
    if header_row not in numbers:
        raise ValueError("DeepSeek 返回的表头行不在预览范围内")
    column_count = max(len(item["cells"]) for item in preview["rows"])
    raw = answer.get("mapping") if isinstance(answer.get("mapping"), dict) else {}
    # 超范围、无法解析或与其他字段占同一列的建议列为“丢弃”，必须提示而不是静默吞掉
    mapping: dict[str, int] = {}
    occupied: dict[int, str] = {}
    dropped: list[str] = []
    for field in KIND_FIELDS[kind]:
        if field not in raw:
            continue
        try:
            index = int(raw[field]) - 1
        except (TypeError, ValueError):
            dropped.append(f"“{field}”的列号无法解析（{raw[field]!r}），已舍弃")
            continue
        if not 0 <= index < column_count:
            dropped.append(f"“{field}”建议的第 {index + 1} 列超出预览范围，已舍弃")
            continue
        if index in occupied:
            dropped.append(f"“{field}”与“{occupied[index]}”建议了同一列，已保留先出现的“{occupied[index]}”")
            continue
        mapping[field] = index
        occupied[index] = field
    extra_fields = [key for key in raw if key not in KIND_FIELDS[kind]]
    if extra_fields:
        dropped.append("以下字段不属于该表类型，已忽略：" + "、".join(sorted(map(str, extra_fields))[:6]))
    warnings = critical_warnings(kind, mapping) + dropped
    try:
        ImportSpec(Path("preview.xlsx"), preview["sheet"], header_row, kind, mapping).validate()
    except ValueError as exc:
        warnings.append(f"当前建议尚不满足导入条件：{exc}")
    return MappingSuggestion(kind, header_row, mapping, {}, tuple(warnings), MODEL)
