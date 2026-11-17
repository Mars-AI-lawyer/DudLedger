"""Small line icons and hover labels for the workspace navigation."""

import math
import tkinter as tk
from tkinter import ttk

from . import theme


# Paths are drawn on a common 20 × 20 grid.
ICONS = {
    "workbench": (((3, 4), (17, 4), (17, 17), (3, 17), (3, 4)), ((7, 2), (13, 2), (13, 6), (7, 6), (7, 2)), ((6, 10), (14, 10)), ((6, 14), (12, 14))),
    "calculator": (((4, 2), (16, 2), (16, 18), (4, 18), (4, 2)), ((7, 5), (13, 5), (13, 8), (7, 8), (7, 5)), ((7, 11), (9, 11)), ((12, 11), (14, 11)), ((7, 15), (9, 15)), ((12, 15), (14, 15))),
    "accounts": (((4, 3), (4, 16), (8, 16)), ((4, 10), (8, 10)), ((4, 4), (8, 4)),
                 ((11, 4), (17, 4)), ((11, 10), (17, 10)), ((11, 16), (17, 16))),
    "entries": (((3, 3), (17, 3), (17, 16), (3, 16), (3, 3)), ((3, 7), (17, 7)),
                ((3, 12), (17, 12)), ((8, 7), (8, 16))),
    "balance": (((10, 2), (10, 17)), ((6, 17), (14, 17)), ((3, 5), (17, 5)),
                ((5, 5), (2, 12), (8, 12), (5, 5)), ((15, 5), (12, 12), (18, 12), (15, 5))),
    "income": (((3, 3), (3, 16), (17, 16)), ((6, 12), (10, 8), (13, 10), (17, 5)),
               ((14, 5), (17, 5), (17, 8))),
    "marks": (((5, 3), (15, 3), (15, 17), (10, 13), (5, 17), (5, 3)),),
    "review": (((9, 3), (11, 8), (16, 10), (11, 12), (9, 17), (7, 12), (2, 10), (7, 8), (9, 3)),
               ((16, 3), (16, 7)), ((14, 5), (18, 5))),
    "parties": (((6, 2), (8, 4), (8, 6), (6, 8), (4, 6), (4, 4), (6, 2)),
                ((2, 16), (2, 13), (5, 11), (8, 11), (11, 13), (11, 16), (2, 16)),
                ((13, 3), (15, 5), (13, 7)), ((13, 11), (16, 11), (18, 13), (18, 16), (14, 16))),
    "settings": (((3, 5), (17, 5)), ((3, 10), (17, 10)), ((3, 15), (17, 15)),
                 ((7, 3), (7, 7)), ((13, 8), (13, 12)), ((9, 13), (9, 17))),
    "folder": (((2, 5), (2, 16), (16, 16), (18, 8), (7, 8), (5, 13)),
               ((2, 5), (7, 5), (9, 7), (16, 7))),
    "expand": (((2, 4), (17, 4), (17, 15), (2, 15), (2, 4)), ((6, 4), (6, 15)),
               ((10, 7), (13, 10), (10, 13))),
    "collapse": (((2, 4), (17, 4), (17, 15), (2, 15), (2, 4)), ((6, 4), (6, 15)),
                 ((13, 7), (10, 10), (13, 13))),
    "filter": (((3, 4), (17, 4), (12, 11), (12, 16), (8, 14), (8, 11), (3, 4)),),
    "search": (((9, 3), (14, 8), (12, 14), (7, 15), (4, 10), (9, 3)), ((13, 13), (17, 17))),
    "close": (((5, 5), (15, 15)), ((15, 5), (5, 15))),
    "chevron-left": (((13, 4), (6, 10), (13, 16)),),
    "chevron-right": (((7, 4), (14, 10), (7, 16)),),
    "expand-all": (((10, 3), (10, 17)), ((5, 8), (10, 13), (15, 8)), ((5, 13), (10, 18), (15, 13))),
    "collapse-all": (((10, 3), (10, 17)), ((5, 7), (10, 2), (15, 7)), ((5, 12), (10, 7), (15, 12))),
    "plus": (((10, 4), (10, 16)), ((4, 10), (16, 10))),
    "dot": (((10, 7), (13, 10), (10, 13), (7, 10), (10, 7)),),
}

INK_SOFT = theme.INK_SOFT  # backwards-compatible alias
ICON_SIZE = 20
ICON_HIT_AREA = 28
TOOLTIP_DELAY_MS = 275


def icon_button_padding(icon_size=ICON_SIZE, minimum=ICON_HIT_AREA):
    """Symmetric padding that guarantees a square minimum pointer target."""
    if icon_size <= 0 or minimum <= 0:
        raise ValueError("图标与命中区尺寸必须为正数")
    padding = max(0, math.ceil((minimum - icon_size) / 2))
    return padding, padding


def navigation_icon(master, name, color=INK_SOFT, *, scale=1, size=None):
    """Draw a crisp line icon on the shared 20×20 grid.

    `scale` remains compatible with existing callers; new controls should use
    `size=16` or `size=20` and leave the hit area to `icon_button_padding`.
    """
    logical_size = int(20 * scale if size is None else size)
    if logical_size <= 0:
        raise ValueError("图标尺寸必须为正数")
    ratio = logical_size / 20 * 2
    image = tk.PhotoImage(master=master, width=logical_size * 2, height=logical_size * 2)
    for path in ICONS[name]:
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            x1, y1, x2, y2 = x1 * ratio, y1 * ratio, x2 * ratio, y2 * ratio
            steps = max(1, math.ceil(max(abs(x2 - x1), abs(y2 - y1))))
            for step in range(steps + 1):
                x = round(x1 + (x2 - x1) * step / steps)
                y = round(y1 + (y2 - y1) * step / steps)
                # 2×2 block keeps line weight proportional after subsampling.
                image.put(color, to=(x, y, x + 2, y + 2))
    return image.subsample(2, 2)


class NavigationTooltip:
    def __init__(self, owner):
        self.owner = owner
        self.pending = None
        self.window = None

    def attach(self, widget, text):
        for event in ("<Enter>", "<FocusIn>"):
            widget.bind(event, lambda _event: self._schedule(widget, text), add="+")
        for event in ("<Leave>", "<FocusOut>", "<ButtonPress-1>", "<Unmap>", "<Destroy>"):
            widget.bind(event, lambda _event: self.hide(), add="+")

    def _schedule(self, widget, text):
        self.hide()
        self.pending = self.owner.after(TOOLTIP_DELAY_MS, self._show, widget, text)

    def _show(self, widget, text):
        self.pending = None
        label = text() if callable(text) else text
        if not label or not widget.winfo_exists() or not widget.winfo_ismapped():
            return
        tip = self.window = tk.Toplevel(self.owner)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.transient(self.owner)
        ttk.Label(tip, text=label, padding=(8, 5), relief="solid", borderwidth=1).pack()
        x = widget.winfo_rootx() + widget.winfo_width() + 6
        y = widget.winfo_rooty()
        tip.geometry(f"{x:+d}{y:+d}")
        tip.deiconify()
        tip.lift()

    def hide(self):
        if self.pending is not None:
            self.owner.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            if self.window.winfo_exists():
                self.window.destroy()
            self.window = None
