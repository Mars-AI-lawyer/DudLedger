"""Shared provider preference and fallback order for voucher review."""

PROVIDER_LABELS = {'bailian': '百炼优先（默认）', 'jev': 'Jev 优先'}


def preferred_provider(value):
    return value if isinstance(value, str) and value in PROVIDER_LABELS else 'bailian'


def provider_order(preferred='bailian', *, bailian_enabled=True):
    order = ('jev', 'bailian', 'deepseek') if preferred_provider(preferred) == 'jev' else ('bailian', 'jev', 'deepseek')
    return tuple(provider for provider in order if provider != 'bailian' or bailian_enabled)


def order_description(preferred='bailian', *, bailian_enabled=True):
    labels = {'bailian': '百炼（已确认免费可用）', 'jev': 'Jev', 'deepseek': 'DeepSeek Flash'}
    text = ' → '.join(labels[provider] for provider in provider_order(preferred, bailian_enabled=bailian_enabled))
    return text if bailian_enabled else text + '（百炼未确认免费，已跳过）'
