"""Canvas-drawn rounded widgets.

ttk (clam) cannot render rounded corners, so primary actions and panels use
these lightweight canvas components. Radius tokens live in theme.py:
RADIUS_CONTROL (4px) for controls, RADIUS_PANEL (12px) for existing cards.
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
                        hover=theme.ACCENT_HOVER, pressed=theme.ACCENT_PRESSED,
                        disabled_bg="#DDD5CB", disabled_fg=theme.INK_SOFT),
        "secondary": dict(bg=theme.SURFACE, fg=theme.INK, outline=theme.BORDER_STRONG,
                          hover=theme.HOVER, pressed=theme.SELECTED,
                          disabled_bg=theme.SUNKEN, disabled_fg=theme.DISABLED),
        "ghost": dict(bg="", fg=theme.INK_SOFT, outline="",
                      hover=theme.HOVER, pressed=theme.SELECTED,
                      disabled_bg="", disabled_fg=theme.DISABLED),
        "danger": dict(bg=theme.SURFACE, fg=theme.DANGER, outline=theme.DANGER,
                       hover=theme.DANGER_SOFT, pressed=theme.DANGER_SOFT,
                       disabled_bg=theme.SUNKEN, disabled_fg=theme.DISABLED),
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
        self._pointer_pressed = False
        self._focused = False
        self._hovered = False
        self._key_pressed = False
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
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        for key in ("Return", "space"):
            self.bind(f"<KeyPress-{key}>", self._on_key_press)
            self.bind(f"<KeyRelease-{key}>", self._on_key_release)
        self.configure(takefocus=1, cursor="hand2")
        self._redraw()

    def _fit(self):
        """Remeasure label and line height after text or font changes."""
        from tkinter import font as tkfont
        f = self._font or tkfont.nametofont("TkDefaultFont")
        if isinstance(f, (str, tuple)):
            f = tkfont.Font(root=self, font=f)
        text_w = f.measure(self._text) if self._text else 0
        if not text_w and self._image is not None:
            text_w = self._image.width()
        line_h = f.metrics("linespace")
        width = text_w + self._padx * 2
        height = line_h + self._pady * 2
        if self._auto_size:
            self.configure(width=width, height=height)
        else:
            self.configure(height=height)

    # -- public API ---------------------------------------------------------
    def configure(self, cnf=None, **kw):
        if cnf:
            kw = {**cnf, **kw}
        remeasure = state_changed = False
        if "variant" in kw:
            variant = kw.pop("variant")
            if variant not in self._VARIANTS:
                raise ValueError(f"未知按钮层级：{variant}")
            self._variant = self._VARIANTS[variant]
            state_changed = True
        if "state" in kw:
            self._state = kw.pop("state")
            kw["cursor"] = "hand2" if self._state == "normal" else "arrow"
            kw["takefocus"] = 1 if self._state == "normal" else 0
            if self._state == "disabled":
                self._pressed = self._key_pressed = self._pointer_pressed = False
            state_changed = True
        if "text" in kw:
            self._text = kw.pop("text")
            remeasure = True
        if "font" in kw:
            self._font = kw.pop("font")
            remeasure = True
        if "image" in kw:
            self._image = kw.pop("image")
            remeasure = True
        result = super().configure(**kw) if kw else None
        if remeasure:
            self._fit()
        if (remeasure or state_changed) and hasattr(self, "_shape_id"):
            self._redraw()
        return result

    config = configure

    def remeasure(self):
        """Public hook for the application's global font-size traversal."""
        self._fit()
        self._redraw()

    def invoke(self):
        if self._state != "disabled" and self._command is not None:
            self._command()

    # -- internals ----------------------------------------------------------
    def _on_enter(self, _event=None):
        self._hovered = True
        self._redraw()

    def _on_leave(self, _event=None):
        self._hovered = self._pointer_pressed = False
        self._pressed = self._key_pressed
        self._redraw()

    def _on_focus_in(self, _event=None):
        self._focused = True
        self._redraw()

    def _on_focus_out(self, _event=None):
        self._focused = self._pressed = self._key_pressed = self._pointer_pressed = False
        self._redraw()

    def _on_key_press(self, _event=None):
        if self._state == "disabled":
            return "break"
        if not self._key_pressed:
            self._key_pressed = self._pressed = True
            self._redraw()
        return "break"

    def _on_key_release(self, _event=None):
        was = self._key_pressed and self._state != "disabled"
        self._key_pressed = False
        self._pressed = getattr(self, "_pointer_pressed", False)
        self._redraw()
        if was:
            self.invoke()
        return "break"

    def _on_press(self, _e):
        if self._state == "disabled":
            return
        self._pointer_pressed = self._pressed = True
        self.focus_set()
        self._redraw()

    def _on_release(self, event):
        if self._state == "disabled":
            return
        was = getattr(self, "_pointer_pressed", False)
        self._pointer_pressed = False
        self._pressed = getattr(self, "_key_pressed", False)
        self._redraw()
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if was and inside:
            self.invoke()

    def _colors(self):
        if self._state == "disabled":
            v = self._variant
            return dict(bg=v["disabled_bg"], fg=v["disabled_fg"], outline=theme.BORDER)
        v = self._variant
        bg = v["bg"]
        if self._pressed:
            bg = v["pressed"] or bg
        elif self._hover():
            bg = v["hover"] or bg
        return dict(bg=bg, fg=v["fg"], outline=v["outline"])

    def _hover(self):
        if getattr(self, "_hovered", False):
            return True
        try:
            x = self.winfo_pointerx() - self.winfo_rootx()
            y = self.winfo_pointery() - self.winfo_rooty()
            return 0 <= x < self.winfo_width() and 0 <= y < self.winfo_height()
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
        inset = 3 if self._focused else 1
        if self._focused and self._state != "disabled" and w > 6 and h > 6:
            self.create_polygon(_rounded_points(1, 1, w - 2, h - 2, self._radius + 1),
                                smooth=True, fill="", outline=theme.FOCUS_RING, width=2,
                                tags="focus-ring")
        if fill != base or colors["outline"]:
            self._shape_id = self.create_polygon(
                _rounded_points(inset, inset, w - inset - 1, h - inset - 1, self._radius),
                smooth=True, fill=fill, outline=colors["outline"], width=1,
                tags="button-shape")
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
