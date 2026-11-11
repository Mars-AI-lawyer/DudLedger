"""Canvas-drawn rounded widgets.

ttk (clam) cannot render rounded corners, so primary actions and panels use
these lightweight canvas components. Radius tokens live in theme.py:
RADIUS_CONTROL (8px) for controls, RADIUS_PANEL (12px) for panels/cards.
"""

import tkinter as tk

from . import theme


def _rounded_points(x1, y1, x2, y2, r):
    """Polygon points approximating a rounded rectangle."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r,
        x2, y2, x2 - r, y2,
        x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r,
        x1, y1, x1 + r, y1,
    ]


class RoundedButton(tk.Canvas):
    """A rounded-corner button with hover/pressed/disabled states.

    Variants: 'primary' (solid accent), 'secondary' (outlined),
    'ghost' (borderless), 'danger' (red outline). Mirrors the ttk button
    hierarchy for places where rounded corners matter (main CTAs).
    """

    _VARIANTS = {
        "primary": dict(bg=theme.ACCENT, fg="#FFFFFF", outline="",
                        hover=theme.ACCENT_HOVER, pressed=theme.ACCENT_PRESSED),
        "secondary": dict(bg=theme.SURFACE, fg=theme.INK, outline=theme.BORDER_STRONG,
                          hover=theme.HOVER, pressed=theme.SELECTED),
        "ghost": dict(bg="", fg=theme.INK_SOFT, outline="",
                      hover=theme.HOVER, pressed=theme.SELECTED),
        "danger": dict(bg=theme.SURFACE, fg=theme.DANGER, outline=theme.DANGER,
                       hover=theme.DANGER_SOFT, pressed=theme.DANGER_SOFT),
    }

    def __init__(self, master, text="", command=None, variant="primary",
                 radius=theme.RADIUS_CONTROL, padx=16, pady=8, font=None,
                 width=None, image=None, **kwargs):
        self._variant = self._VARIANTS[variant]
        self._command = command
        self._text = text
        self._image = image
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._state = "normal"
        self._pressed = False
        self._font = font
        super().__init__(master, highlightthickness=0, bd=0,
                         background=master.cget("background") if "background" in master.keys() else theme.CANVAS,
                         **kwargs)
        self._text_id = None
        self._shape_id = None
        self._auto_size = width is None
        self._fit()
        if width is not None:
            self.configure(width=width)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._redraw())
        self.bind("<Leave>", lambda _e: (setattr(self, "_pressed", False), self._redraw()))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        self.configure(takefocus=1, cursor="hand2")
        self._redraw()

    def _fit(self):
        """Size the canvas to its label (plus padding) when auto-sizing."""
        if not self._auto_size:
            return
        from tkinter import font as tkfont
        f = self._font or tkfont.nametofont("TkDefaultFont")
        text_w = f.measure(self._text) if self._text else 0
        line_h = f.metrics("linespace")
        self.configure(width=text_w + self._padx * 2, height=line_h + self._pady * 2)

    # -- public API ---------------------------------------------------------
    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self._state = kw.pop("state")
            self.configure(cursor="hand2" if self._state == "normal" else "arrow")
            self._redraw()
        if "text" in kw:
            self._text = kw.pop("text")
            self._fit()
            self._redraw()
        return super().configure(cnf, **kw) if (cnf or kw) else None

    config = configure

    def invoke(self):
        if self._state != "disabled" and self._command is not None:
            self._command()

    # -- internals ----------------------------------------------------------
    def _on_press(self, _e):
        if self._state == "disabled":
            return
        self._pressed = True
        self.focus_set()
        self._redraw()

    def _on_release(self, event):
        if self._state == "disabled":
            return
        was = self._pressed
        self._pressed = False
        self._redraw()
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if was and inside:
            self.invoke()

    def _colors(self):
        if self._state == "disabled":
            v = self._variant
            return dict(bg=v["bg"] or self.cget("background"), fg=theme.DISABLED, outline=theme.BORDER)
        v = self._variant
        bg = v["bg"]
        if self._pressed:
            bg = v["pressed"] or bg
        elif self._hover():
            bg = v["hover"] or bg
        return dict(bg=bg, fg=v["fg"], outline=v["outline"])

    def _hover(self):
        try:
            x = self.winfo_pointerx() - self.winfo_rootx()
            y = self.winfo_pointery() - self.winfo_rooty()
            return 0 <= x <= self.winfo_width() and 0 <= y <= self.winfo_height()
        except tk.TclError:
            return False

    def _redraw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w = self.winfo_width() or (self._padx * 2 + 60)
        h = self.winfo_height() or (self._pady * 2 + 16)
        colors = self._colors()
        base = self.cget("background")
        fill = colors["bg"] or base
        if fill != base or colors["outline"]:
            self._shape_id = self.create_polygon(
                _rounded_points(1, 1, w - 1, h - 1, self._radius),
                smooth=True, fill=fill, outline=colors["outline"], width=1)
        label = self._text
        self._text_id = self.create_text(w / 2, h / 2, text=label, fill=colors["fg"],
                                         font=self._font, image=self._image)


class RoundedFrame(tk.Canvas):
    """A rounded-corner container. Pack/grid children into `.body`."""

    def __init__(self, master, radius=theme.RADIUS_PANEL, fill=theme.SURFACE,
                 outline=theme.BORDER_STRONG, padding=theme.SPACE_3, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self.body = tk.Frame(self, background=fill, bd=0)
        self.create_window(padding, padding, window=self.body, anchor="nw", tags="body")
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("shape")
        self.create_polygon(_rounded_points(1, 1, w - 1, h - 1, self._radius),
                            smooth=True, fill=self._fill, outline=self._outline,
                            width=1, tags="shape")
        self.tag_lower("shape", "body")
