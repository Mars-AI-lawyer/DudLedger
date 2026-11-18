"""Pure helpers for issue chooser ordering and drag-to-issue sessions."""


def ordered_issue_choices(items, query='', current_id=None, recent_ids=()):
    """Filter by title/status, then pin current and recently used issues."""
    needle = query.strip().casefold()
    recent = {issue_id: index for index, issue_id in enumerate(recent_ids)}
    filtered = [item for item in items
                if not needle or needle in f'{item["title"]} {item["status"]}'.casefold()]
    return sorted(filtered, key=lambda item: (
        0 if item['id'] == current_id else 1 if item['id'] in recent else 2,
        recent.get(item['id'], 0),
        item['id'],
    ))


TOOL_GRIP_WIDTH = 12


def tool_width_limits(available, mode):
    usable = max(2, available - TOOL_GRIP_WIDTH)
    tool_minimum = min(300 if mode == 'issues' else 260, max(1, usable * 3 // 8))
    main_minimum = min(400, usable // 2)
    return tool_minimum, usable - main_minimum


def link_feedback(title, result):
    return (f'已加入“{title}”：新增 {result["added"]} 张，'
            f'已存在 {result["duplicates"]} 张（重复），同步关注 {result.get("attention", 0)} 张；'
            '已有备注未覆盖。')


def drag_handle_at(column, x, region):
    """Reserve the left checkbox and all data cells for their existing gestures."""
    return column == '#0' and x >= 30 and region != 'separator'


class VoucherDragSession:
    """UI-independent state machine; invalid drops and cancellation never commit."""

    def __init__(self, resolve, commit, present, finish):
        self.resolve = resolve
        self.commit = commit
        self.present = present
        self.finish = finish
        self.keys = ()
        self.target = None
        self.active = False

    def start(self, keys, x_root, y_root):
        keys = tuple(dict.fromkeys(key for key in keys if key))
        if self.active or not keys:
            return False
        self.keys = keys
        self.active = True
        self.move(x_root, y_root)
        return True

    def move(self, x_root, y_root):
        if not self.active:
            return None
        self.target = self.resolve(x_root, y_root, self.keys)
        self.present(self.keys, self.target, x_root, y_root)
        return self.target

    def drop(self, x_root, y_root):
        if not self.active:
            return False
        keys = self.keys
        try:
            target = self.resolve(x_root, y_root, keys)
            committed = bool(target and self.commit(target, keys))
        except Exception:
            self._finish(False)
            raise
        self._finish(committed)
        return committed

    def cancel(self):
        if not self.active:
            return False
        self._finish(False)
        return True

    def _finish(self, committed):
        self.active = False
        self.keys = ()
        self.target = None
        self.finish(committed)
