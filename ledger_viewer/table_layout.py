"""Temporarily fit workspace columns while a side tool is open."""


# These are reading widths, not global Treeview minima: original widths are
# restored when the tool closes.  Other columns may shrink to their own minima
# first, and the horizontal scrollbar handles any remaining overflow.
READABLE_MINIMA = {
    'summary': 220,
    'counterparty': 160,
}
AMOUNT_COLUMNS = {
    'amount', 'debit', 'credit', 'original',
    'od', 'oc', 'pd', 'pc', 'yd', 'yc', 'cd', 'cc',
    'left_amount_1', 'left_amount_2', 'left_amount_3',
    'right_amount_1', 'right_amount_2', 'right_amount_3',
    'left_a', 'left_b', 'left_c', 'right_a', 'right_b', 'right_c', 'a', 'b',
}


def readable_minimum(key, minimum):
    """Protect narrative, counterparty and money columns differently."""
    protected = 112 if key in AMOUNT_COLUMNS else READABLE_MINIMA.get(key, 0)
    return max(minimum, protected)


class WorkspaceTableFit:
    def __init__(self, app, views):
        self.app = app
        self.views = views
        self.saved = {}
        self.applied = {}
        self.scroll = {}
        self.job = None
        for view in views:
            view.bind('<Configure>', self.schedule, add='+')

    @staticmethod
    def columns(view):
        columns = tuple(view.cget('displaycolumns'))
        if columns == ('#all',):
            columns = tuple(view.cget('columns'))
        return (('#0',) if 'tree' in tuple(map(str, view.cget('show'))) else ()) + columns

    def minimum_width(self):
        view = next((view for view in self.views if view.winfo_ismapped()), None)
        if view is None:
            return 525
        minimum = max(525, sum(view.column(key, 'minwidth') for key in self.columns(view)) + 24)
        navigation = getattr(self.app, 'detail_accounts', None)
        if view is self.app.detail_view and navigation and navigation.winfo_ismapped():
            minimum += navigation.winfo_width() + 6
        return minimum

    def schedule(self, _event=None):
        if self.job is None:
            self.job = self.app.after_idle(self.fit)

    def fit(self):
        self.job = None
        if not self.app.workbench.visible or getattr(self.app.workbench, 'overlay', False):
            # Overlay drawers cover auxiliary space; they never compress the ledger.
            for view, widths in self.saved.items():
                for key, width in widths.items():
                    view.column(key, width=width)
                view.xview_moveto(self.scroll[view])
            self.saved.clear()
            self.applied.clear()
            self.scroll.clear()
            return
        for view in self.views:
            if not view.winfo_ismapped() or view.winfo_width() <= 1:
                continue
            keys = self.columns(view)
            if view not in self.saved:
                self.scroll[view] = view.xview()[0]
            saved = self.saved.setdefault(view, {})
            previous = self.applied.setdefault(view, {})
            for key in keys:
                current = view.column(key, 'width')
                if key not in saved or (key in previous and current != previous[key]):
                    saved[key] = current
            minima = {key: readable_minimum(key, view.column(key, 'minwidth')) for key in keys}
            space = max(0, view.winfo_width() - 2 - sum(minima.values()))
            flexible = {key: max(0, saved[key] - minima[key]) for key in keys}
            total = sum(flexible.values())
            # Keep original widths when they already fit; otherwise distribute
            # the remaining space without shrinking fonts or hiding columns.
            fraction = min(1, space / total) if total else 0
            for key in keys:
                width = minima[key] + int(flexible[key] * fraction)
                view.column(key, width=width)
                previous[key] = width
            if sum(view.column(key, 'width') for key in keys) <= view.winfo_width() - 2:
                view.xview_moveto(0)
