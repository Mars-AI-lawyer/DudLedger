"""Subtle dashed accounting grid for Tk Treeviews."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class DashedGrid:
    """Draw one-pixel dashed separators over visible rows and columns only."""

    def __init__(self, view: ttk.Treeview, color: str = "#DCE4E5", background: str = "#FFFFFF"):
        self.view = view
        self.color = color
        self.background = background
        self.horizontal: list[tk.Canvas] = []
        self.vertical: list[tk.Canvas] = []
        self.pending = False
        for event in ("<Configure>", "<Map>", "<ButtonRelease-1>"):
            view.bind(event, self.schedule, add="+")

    def schedule(self, _event: object = None) -> None:
        if self.pending or not self.view.winfo_exists():
            return
        self.pending = True
        self.view.after_idle(self.draw)

    def _line(self, pool: list[tk.Canvas], index: int) -> tk.Canvas:
        while len(pool) <= index:
            pool.append(tk.Canvas(self.view, highlightthickness=0, borderwidth=0, background=self.background))
        return pool[index]

    def draw(self) -> None:
        self.pending = False
        if not self.view.winfo_exists():
            return
        width, height = self.view.winfo_width(), self.view.winfo_height()
        first = next((self.view.identify_row(y) for y in range(1, height, 4) if self.view.identify_row(y)), "")
        if not first:
            self._hide_all()
            return
        visible = []
        item = first
        while item and len(visible) < 80:
            box = self.view.bbox(item)
            if box and box[1] > height:
                break
            if box and box[1] + box[3] > 0:
                visible.append((item, box))
            item = self.view.next(item)
        if not visible:
            self._hide_all()
            return
        horizontal_used = 0
        for _, box in visible:
            y = box[1] + box[3] - 1
            if not 0 < y < height:
                continue
            line = self._line(self.horizontal, horizontal_used)
            line.configure(width=width, height=1)
            line.delete("all")
            line.create_line(0, 0, width, 0, fill=self.color, dash=(2, 4))
            line.place(x=0, y=y)
            horizontal_used += 1
        for line in self.horizontal[horizontal_used:]:
            line.place_forget()
        display = self.view.cget("displaycolumns")
        columns = self.view.cget("columns") if display == "#all" else display
        if isinstance(columns, str):
            columns = self.view.tk.splitlist(columns)
        top = max(0, visible[0][1][1])
        bottom = min(height, visible[-1][1][1] + visible[-1][1][3])
        used = 0
        for number, _ in enumerate(columns, 1):
            box = self.view.bbox(visible[0][0], f"#{number}")
            if not box:
                continue
            x = box[0] + box[2] - 1
            if not 0 < x < width:
                continue
            line = self._line(self.vertical, used)
            line.configure(width=1, height=max(1, bottom - top))
            line.delete("all")
            line.create_line(0, 0, 0, bottom - top, fill=self.color, dash=(2, 4))
            line.place(x=x, y=top)
            used += 1
        for line in self.vertical[used:]:
            line.place_forget()

    def _hide_all(self) -> None:
        for line in (*self.horizontal, *self.vertical):
            line.place_forget()
