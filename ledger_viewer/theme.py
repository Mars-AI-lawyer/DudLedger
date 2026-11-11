"""Warm, quiet desktop styling shared by the ledger workspace and dialogs."""

import tkinter as tk
from tkinter import font as tkfont, ttk


CANVAS = "#FAF9F5"
SIDEBAR = "#F0EEE6"
INK = "#30302C"
MUTED = "#77766F"
BORDER = "#E3E0D7"
ACCENT = "#A65036"


def configure_theme(root):
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*Background", CANVAS)
    root.option_add("*Text.background", "#FFFFFF")
    root.option_add("*Text.foreground", INK)
    root.option_add("*Text.insertBackground", INK)
    root.option_add("*Text.selectBackground", "#E8DECE")
    root.option_add("*Text.selectForeground", INK)
    root.option_add("*Text.font", "TkTextFont")
    style.configure(".", background=CANVAS, foreground=INK, bordercolor=BORDER,
                    lightcolor=CANVAS, darkcolor=BORDER, focuscolor=ACCENT)
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        style.configure(name, background=CANVAS)
    style.configure("Hint.TLabel", foreground=MUTED)
    style.configure("TButton", padding=(10, 6), background="#F0EEE8", foreground=INK,
                    borderwidth=0, relief="flat", focusthickness=1, focuscolor=BORDER)
    style.map("TButton", background=[("disabled", "#F2F0EB"), ("pressed", "#DEDAD1"), ("active", "#E6E3DA")],
              foreground=[("disabled", "#9C9A92")])
    style.configure('Quiet.TButton', background=CANVAS, foreground=MUTED, padding=(0, 3))
    style.configure('Resize.TFrame', background=BORDER)
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", padding=(12, 7), focuscolor="#E6BCAB")
    style.map("Accent.TButton", background=[("disabled", "#DDD5CB"), ("pressed", "#82412D"), ("active", "#92482F")],
              foreground=[("disabled", "#77766F"), ("!disabled", "#FFFFFF")])
    style.configure("TMenubutton", background=CANVAS, padding=(8, 6), relief="flat", borderwidth=0)
    style.configure("TEntry", fieldbackground="#FFFFFF", padding=6, borderwidth=1, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TCombobox", fieldbackground="#FFFFFF", background="#F0EEE8", padding=5, arrowsize=12)
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")],
              selectbackground=[("readonly", "#FFFFFF")], selectforeground=[("readonly", INK)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=CANVAS, padding=3)
        style.map(name, background=[("active", CANVAS)])
    # Clam's native selected indicator is a cross. Use an explicit tick while
    # retaining Checkbutton keyboard behavior and BooleanVar semantics.
    root.sampling_check_images = []
    for selected in (False, True):
        icon = tk.PhotoImage(master=root, width=22, height=20)
        icon.put('#FFFFFF', to=(2, 2, 18, 18))
        border = '#58816B' if selected else '#A8A69E'
        for box in ((2, 2, 18, 3), (2, 17, 18, 18), (2, 2, 3, 18), (17, 2, 18, 18)):
            icon.put(border, to=box)
        if selected:
            for x, y in ((5, 10), (6, 11), (7, 12), (8, 13), (9, 12), (10, 11), (11, 10), (12, 9), (13, 8), (14, 7)):
                icon.put('#356348', to=(x, y - 1, x + 2, y + 1))
        root.sampling_check_images.append(icon)
    style.element_create('Sampling.check', 'image', root.sampling_check_images[0],
                         ('selected', root.sampling_check_images[1]))
    style.layout('Sampling.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Sampling.check', {'side': 'left', 'sticky': ''}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    style.configure('Segment.TRadiobutton', padding=(12, 7), background=SIDEBAR)
    style.map('Segment.TRadiobutton', background=[('selected', '#E2DED3'), ('active', '#E8E4DB')], foreground=[('selected', '#873F2C')])
    style.configure("TSeparator", background=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TNotebook", background=CANVAS, borderwidth=0)
    style.configure("TNotebook.Tab", background=SIDEBAR, padding=(12, 7))
    style.map("TNotebook.Tab", background=[("selected", CANVAS)])
    style.layout("Workspace.TNotebook.Tab", [])
    style.configure("Workspace.TNotebook", tabmargins=0, borderwidth=0, lightcolor=CANVAS, darkcolor=CANVAS, bordercolor=CANVAS)
    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=INK,
                    borderwidth=0, relief="flat", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Treeview.Heading", background="#F1EFE9", foreground="#65635C", padding=(8, 8), relief="flat")
    style.map("Treeview", background=[("selected", "#E8DECE")], foreground=[("selected", INK)])
    style.map("Treeview.Heading", background=[("active", "#E9E5DC")])
    for axis in ("Vertical", "Horizontal"):
        style.configure(f"{axis}.TScrollbar", background="#D4D0C6", troughcolor=CANVAS,
                        borderwidth=0, arrowsize=12, relief="flat", lightcolor=CANVAS, darkcolor=CANVAS,
                        bordercolor=CANVAS)
    style.configure("TProgressbar", background=ACCENT, troughcolor=SIDEBAR, borderwidth=0)
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("Sidebar.TLabel", background=SIDEBAR)
    style.configure("SidebarHint.TLabel", background=SIDEBAR, foreground=MUTED)
    style.configure("Nav.TButton", background=SIDEBAR, anchor="w", padding=(10, 4), focuscolor="#B5AEA0")
    style.map("Nav.TButton", background=[("selected", "#E2DED3"), ("active", "#E8E4DB")],
              foreground=[("selected", "#873F2C"), ("!selected", "#53524C")])
    style.configure("Rail.Nav.TButton", anchor="center", padding=(0, 7))
    style.configure("Status.TLabel", foreground=MUTED, padding=(18, 7))
    base = tkfont.nametofont("TkDefaultFont")
    size = abs(base.cget("size"))
    root.intro_font = tkfont.Font(root=root, family=base.cget("family"), size=size + 3, weight="bold")
    root.welcome_font = tkfont.Font(root=root, family=base.cget("family"), size=size + 13)
    style.configure("Intro.TLabel", font=root.intro_font)
    style.configure("Brand.TLabel", background=SIDEBAR, font=root.intro_font)
    style.configure("Welcome.TLabel", font=root.welcome_font)
