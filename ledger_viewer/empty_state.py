"""Centered empty-state panel used across pages.

Shown as an overlay on top of a page's table area when there is no data,
replacing long-lived hint labels with actionable guidance.
"""

import tkinter as tk
from tkinter import ttk

from . import theme
from .navigation import navigation_icon
from .widgets import RoundedButton


class EmptyState(ttk.Frame):
    """icon + title + hint + optional primary action, vertically centered."""

    def __init__(self, parent, icon, title, hint, action_label=None, action=None):
        super().__init__(parent)
        body = ttk.Frame(self)
        body.place(relx=0.5, rely=0.42, anchor="center")
        self._image = navigation_icon(self, icon, color=theme.MUTED, size=20)
        tk.Label(body, image=self._image, background=theme.CANVAS).pack(pady=(0, theme.SPACE_3))
        ttk.Label(body, text=title, style="Title.TLabel").pack()
        ttk.Label(body, text=hint, style="Hint.TLabel", wraplength=360, justify="center").pack(pady=(theme.SPACE_2, 0))
        if action_label and action:
            RoundedButton(body, text=action_label, variant="primary", command=action).pack(pady=(theme.SPACE_4, 0))

    def show(self):
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()

    def hide(self):
        self.place_forget()
