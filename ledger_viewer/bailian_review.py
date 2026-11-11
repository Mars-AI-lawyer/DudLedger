"""Bailian decision-model-preview transport and secure credentials."""
from __future__ import annotations

import copy
import json
import re
import urllib.error
import urllib.request
from types import SimpleNamespace
from urllib.parse import urlsplit

import keyring

from .deepseek_mapping import _ca_context

MODEL = 'decision-model-preview'
DEFAULT_ENDPOINT = 'https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone'
KEYRING_SERVICE = 'DudLedger Bailian Decision'
KEYRING_ACCOUNT = 'bailian_decision_api_key'

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


def validate_endpoint(endpoint):
    parsed = urlsplit(endpoint.strip())
    allowed = parsed.hostname in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com') or bool(
        re.fullmatch(r'[a-zA-Z0-9-]+\.(?:cn-beijing|ap-southeast-1)\.maas\.aliyuncs\.com', parsed.hostname or ''))
    if not allowed or parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/compatible-mode/v1/systemone':
        raise ValueError('请填写百炼官方 HTTPS 决策接口地址，路径须为 /compatible-mode/v1/systemone')
    return endpoint.strip()


class BailianAPIError(Exception):
    def __init__(self, status_code, code=''):
        self.status_code = status_code
        # Keep only a machine error code; never propagate a remote request/error body.
        self.code = code if re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', str(code)) else ''
        super().__init__('Bailian decision request failed')


def post(api_key, payload, endpoint):
    endpoint = validate_endpoint(endpoint)
    shared_gateway = urlsplit(endpoint).hostname in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com')
    # Alibaba's shared gateway uses the raw key; workspace gateways use Bearer.
    authorization = api_key if shared_gateway else f'Bearer {api_key}'
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': authorization}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=25, context=_ca_context()) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        code = ''
        try:
            data = json.loads(exc.read(8192).decode('utf-8'))
            code = data.get('code') or data.get('error', {}).get('code') or ''
        except (ValueError, TypeError, AttributeError):
            pass
        raise BailianAPIError(exc.code, code) from None
    except (urllib.error.URLError, OSError):
        raise ConnectionError('Bailian connection failed') from None


class DecisionClient:
    """Adapter for the shared review pipeline; split the native 16-question limit."""

    def __init__(self, *, api_key, model=MODEL, endpoint=DEFAULT_ENDPOINT, post_fn=post,
                 cancelled=None, before_send=None, **_options):
        self.key = api_key
        self.model = model
        self.endpoint = validate_endpoint(endpoint)
        self.post_fn = post_fn
        self.cancelled = cancelled
        self.before_send = before_send

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.key = ''

    def system_one(self, *, state, questions):
        answers, input_tokens = {}, 0
        items = list(questions.items())
        for start in range(0, len(items), 16):
            if self.cancelled and self.cancelled():
                raise ReviewCancelled()
            if self.before_send is not None and not self.before_send():
                raise ReviewSnapshotChanged()
            subset = dict(items[start:start + 16])
            payload = {'model': self.model, 'state': state, 'questions': {
                key: question.model_dump(mode='json', exclude_none=True) for key, question in subset.items()}}
            raw = self.post_fn(self.key, payload, self.endpoint)
            if not isinstance(raw, dict) or raw.get('model') != self.model or not isinstance(raw.get('answers'), dict):
                raise ValueError('百炼返回模型或结构无效')
            if set(raw['answers']) != set(subset):
                raise ValueError('百炼返回的问题编号不完整')
            tokens = raw.get('usage', {}).get('input_tokens')
            if type(tokens) is not int or tokens < 0:
                raise ValueError('百炼返回用量无效')
            input_tokens += tokens
            answers.update(raw['answers'])
        result = {'model': self.model, 'answers': answers,
                  'usage': {'input_tokens': input_tokens, 'output_tokens': 0}}
        return SimpleNamespace(model_dump=lambda **_kwargs: copy.deepcopy(result))


class ReviewCancelled(Exception):
    pass


class ReviewSnapshotChanged(Exception):
    pass


def test_connection(api_key, endpoint=DEFAULT_ENDPOINT, post_fn=post):
    from .jev_review import request_review
    from .specialist_rules import default_rules, rules_for_accounts
    from threading import Event
    rules = rules_for_accounts(default_rules(), ['其他应付款'])
    rules['model'] = MODEL
    return request_review([{'entry_date': '2026-01-05', 'name': '其他应付款',
        'summary': '虚构样例：收到正常业务押金', 'credit': '10.00'}], rules, api_key, Event(),
        lambda **options: DecisionClient(**options, endpoint=endpoint, post_fn=post_fn))
