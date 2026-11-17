"""Configurable voucher actions with safeguards for text entry and dialogs."""

DEFAULT_SHORTCUTS = {'mark': 'Shift+F', 'join': 'Shift+G'}
SHORTCUT_LABELS = {'mark': '关注凭证', 'join': '加入问题工作台'}
MODIFIERS = ('Ctrl', 'Alt', 'Shift', 'Cmd')


def normalize_shortcut(value):
    parts = [part.strip() for part in value.split('+')]
    aliases = {'control': 'Ctrl', 'ctrl': 'Ctrl', 'command': 'Cmd', 'cmd': 'Cmd',
               'alt': 'Alt', 'option': 'Alt', 'shift': 'Shift'}
    if len(parts) < 2 or not parts[-1]:
        raise ValueError('请使用组合键，例如 Shift+F 或 Ctrl+Shift+F。')
    modifiers = [aliases.get(part.lower()) for part in parts[:-1]]
    key = parts[-1].upper()
    if None in modifiers or len(set(modifiers)) != len(modifiers):
        raise ValueError('组合键可使用 Ctrl、Alt、Shift、Cmd，且不能重复。')
    if not (len(key) == 1 and key.isascii() and key.isalnum()):
        raise ValueError('最后一个按键请使用英文字母或数字。')
    # Avoid taking over the application's existing navigation and system actions.
    normalized = '+'.join([m for m in MODIFIERS if m in modifiers] + [key])
    if normalized in {'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+A', 'Ctrl+Z', 'Ctrl+O', 'Ctrl+I',
                      'Cmd+C', 'Cmd+V', 'Cmd+X', 'Cmd+A', 'Cmd+Z', 'Cmd+O', 'Cmd+I',
                      'Ctrl+Shift+L', 'Shift+Cmd+L', 'Cmd+Q', 'Cmd+W', 'Alt+F4'}:
        raise ValueError('此快捷键已用于常见编辑或窗口操作，请换一个组合。')
    return normalized


def validate_shortcuts(values):
    normalized = {key: normalize_shortcut(values[key]) for key in DEFAULT_SHORTCUTS}
    if len(set(normalized.values())) != len(normalized):
        raise ValueError('两个操作不能使用相同的快捷键。')
    return normalized


def configured_shortcuts(preferences):
    try:
        return validate_shortcuts({**DEFAULT_SHORTCUTS, **preferences.get('voucher_shortcuts', {})})
    except (ValueError, TypeError, AttributeError):
        return dict(DEFAULT_SHORTCUTS)


def event_shortcut(event, macos=False):
    key = str(event.keysym).upper()
    if len(key) != 1 or not key.isascii() or not key.isalnum():
        return None
    state = event.state
    active = {'Ctrl': bool(state & 4), 'Alt': bool(state & (16 if macos else 0x20008)),
              'Shift': bool(state & 1), 'Cmd': macos and bool(state & 8)}
    return '+'.join([m for m in MODIFIERS if active[m]] + [key])


class VoucherShortcuts:
    def __init__(self, app):
        self.app = app
        self.windows = set()

    def attach(self, window):
        if window in self.windows:
            return
        self.windows.add(window)
        window.bind('<KeyPress>', self.dispatch, add='+')
        window.bind('<Destroy>', lambda event: self.windows.discard(window) if event.widget is window else None, add='+')

    def dispatch(self, event):
        app = self.app
        if not app.store or event.widget.winfo_class() in ('Entry', 'TEntry', 'Text', 'TCombobox', 'Spinbox', 'TSpinbox'):
            return
        # A modal picker or editor owns the keyboard until it is dismissed.
        if app.grab_current() is not None:
            return
        keys = configured_shortcuts(app.controller.preferences)
        pressed = event_shortcut(event, app.tk.call('tk', 'windowingsystem') == 'aqua')
        action = next((key for key, value in keys.items() if value == pressed), None)
        if action is None:
            return
        top = event.widget.winfo_toplevel()
        sources = app.voucher_actions.sources
        view = event.widget if event.widget in sources else None
        if top is not app:
            view = getattr(top, 'voucher_view', None)
            if view is None:
                return
        elif view is None:
            page = app.current_page()
            view = app.detail_view if page == 'entries' else app.mark_view if page == 'marks' else None
        if view is None:
            return
        if top is not app:
            selected = (top.voucher_key,)
        else:
            selected = app.voucher_actions.keys_from_view(view)
        if not selected:
            return
        if action == 'mark':
            app.mark_voucher_keys(selected)
        else:
            app.workbench.choose_issue(selected)
        return 'break'
