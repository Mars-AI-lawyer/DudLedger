"""Warm, quiet desktop styling shared by the ledger workspace and dialogs.

Design tokens are centralized here: colors, fonts, spacing, radius.
All widgets should import tokens from this module instead of hardcoding values.
"""

import tkinter as tk
from tkinter import font as tkfont, ttk


# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------

# Background layers
CANVAS = "#FAF9F5"          # main window background
SIDEBAR = "#F0EEE6"         # sidebar background
SURFACE = "#FFFFFF"         # tables, entries, cards
SUNKEN = "#F5F3EC"          # toolbars, table headers, recessed areas
HOVER = "#ECE9E1"           # generic hover fill (ghost buttons, list rows)
SELECTED = "#E2DED3"        # nav / segment selected fill
ACCENT_SOFT = "#F1E3DD"     # terracotta 10% tint (selected rows, badges)

# Text layers
INK = "#30302C"             # primary text
INK_SOFT = "#53524C"        # secondary text
MUTED = "#77766F"           # hint text
DISABLED = "#9C9A92"        # disabled text

# Border layers
BORDER = "#E3E0D7"          # default hairline
BORDER_STRONG = "#D4D0C6"   # entry / button outline

# Accent & status
ACCENT = "#A65036"          # terracotta primary
ACCENT_HOVER = "#92482F"
ACCENT_PRESSED = "#82412D"
ACCENT_INK = "#873F2C"      # selected-state text
SUCCESS = "#356348"
WARNING = "#8A6D1A"
DANGER = "#9E3B2E"
DANGER_SOFT = "#F7E9E6"

# ---------------------------------------------------------------------------
# Radius & spacing tokens (used by widgets.py canvas-drawn components)
# ---------------------------------------------------------------------------

RADIUS_CONTROL = 8          # buttons, entries, chips
RADIUS_PANEL = 12           # panels, cards, dialogs

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_6 = 24

PAD_BUTTON = (12, 5)                   # secondary buttons
PAD_PRIMARY = (12, 7)                  # primary buttons
PAD_TOOLBAR = (SPACE_4, SPACE_3)       # page toolbars
PAD_PAGE = (SPACE_4, SPACE_3)          # content area margins


def _mono_family():
    """Pick a monospace family available on the platform."""
    preferred = ("Menlo", "Consolas", "Courier New", "TkFixedFont")
    available = set(tkfont.families())
    for name in preferred:
        if name in available:
            return name
    return "TkFixedFont"


def configure_theme(root):
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*Background", CANVAS)
    root.option_add("*Text.background", SURFACE)
    root.option_add("*Text.foreground", INK)
    root.option_add("*Text.insertBackground", INK)
    root.option_add("*Text.selectBackground", ACCENT_SOFT)
    root.option_add("*Text.selectForeground", INK)
    root.option_add("*Text.font", "TkTextFont")
    style.configure(".", background=CANVAS, foreground=INK, bordercolor=BORDER,
                    lightcolor=CANVAS, darkcolor=BORDER, focuscolor=ACCENT)
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        style.configure(name, background=CANVAS)
    style.configure("Hint.TLabel", foreground=MUTED)

    # --- Button hierarchy -------------------------------------------------
    # Secondary (default TButton): white surface + thin outline
    style.configure("TButton", padding=PAD_BUTTON, background=SURFACE, foreground=INK,
                    borderwidth=1, relief="solid", bordercolor=BORDER_STRONG,
                    lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                    focusthickness=1, focuscolor=ACCENT)
    style.map("TButton",
              background=[("disabled", SUNKEN), ("pressed", SELECTED), ("active", HOVER)],
              foreground=[("disabled", DISABLED)],
              bordercolor=[("focus", ACCENT)])
    # Primary: solid terracotta, exactly one per view
    primary_map_bg = [("disabled", "#DDD5CB"), ("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)]
    primary_map_fg = [("disabled", MUTED), ("!disabled", "#FFFFFF")]
    style.configure("Primary.TButton", background=ACCENT, foreground="#FFFFFF",
                    padding=PAD_PRIMARY, borderwidth=0, relief="flat", focuscolor="#E6BCAB")
    style.map("Primary.TButton", background=primary_map_bg, foreground=primary_map_fg)
    # Accent.TButton kept as a full alias of Primary (test compatibility)
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                    padding=PAD_PRIMARY, borderwidth=0, relief="flat", focuscolor="#E6BCAB")
    style.map("Accent.TButton", background=primary_map_bg, foreground=primary_map_fg)
    # Ghost: borderless, hover reveals a soft fill
    style.configure("Ghost.TButton", padding=(SPACE_2, 5), background=CANVAS,
                    foreground=INK_SOFT, borderwidth=0, relief="flat", focuscolor=ACCENT)
    style.map("Ghost.TButton",
              background=[("disabled", CANVAS), ("pressed", SELECTED), ("active", HOVER)],
              foreground=[("disabled", DISABLED)])
    # Danger: destructive actions, red outline + red text
    style.configure("Danger.TButton", padding=PAD_BUTTON, background=SURFACE,
                    foreground=DANGER, borderwidth=1, relief="solid",
                    bordercolor=DANGER, lightcolor=DANGER, darkcolor=DANGER,
                    focusthickness=1, focuscolor=DANGER)
    style.map("Danger.TButton",
              background=[("disabled", SUNKEN), ("pressed", DANGER_SOFT), ("active", DANGER_SOFT)],
              foreground=[("disabled", DISABLED)],
              bordercolor=[("disabled", BORDER)])
    style.configure('Quiet.TButton', background=CANVAS, foreground=MUTED, padding=(0, 3))
    style.configure('Resize.TFrame', background=BORDER)

    style.configure("TMenubutton", background=CANVAS, padding=(8, 6), relief="flat", borderwidth=0)
    style.configure("TEntry", fieldbackground=SURFACE, padding=6, borderwidth=1,
                    bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=SURFACE, background="#F0EEE8", padding=5, arrowsize=12)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
              selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", INK)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=CANVAS, padding=3)
        style.map(name, background=[("active", CANVAS)])
    # Clam's native selected indicator is a cross. Use an explicit tick while
    # retaining Checkbutton keyboard behavior and BooleanVar semantics.
    root.sampling_check_images = []
    for selected in (False, True):
        icon = tk.PhotoImage(master=root, width=22, height=20)
        icon.put(SURFACE, to=(2, 2, 18, 18))
        border = '#58816B' if selected else '#A8A69E'
        for box in ((2, 2, 18, 3), (2, 17, 18, 18), (2, 2, 3, 18), (17, 2, 18, 18)):
            icon.put(border, to=box)
        if selected:
            for x, y in ((5, 10), (6, 11), (7, 12), (8, 13), (9, 12), (10, 11), (11, 10), (12, 9), (13, 8), (14, 7)):
                icon.put(SUCCESS, to=(x, y - 1, x + 2, y + 1))
        root.sampling_check_images.append(icon)
    style.element_create('Sampling.check', 'image', root.sampling_check_images[0],
                         ('selected', root.sampling_check_images[1]))
    style.layout('Sampling.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Sampling.check', {'side': 'left', 'sticky': ''}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    style.configure('Segment.TRadiobutton', padding=(14, 7), background=SIDEBAR)
    style.map('Segment.TRadiobutton', background=[('selected', ACCENT_SOFT), ('active', '#E8E4DB')], foreground=[('selected', ACCENT_INK)])
    style.configure("TSeparator", background=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TNotebook", background=CANVAS, borderwidth=0)
    style.configure("TNotebook.Tab", background=SIDEBAR, padding=(12, 7))
    style.map("TNotebook.Tab", background=[("selected", CANVAS)])
    style.layout("Workspace.TNotebook.Tab", [])
    style.configure("Workspace.TNotebook", tabmargins=0, borderwidth=0, lightcolor=CANVAS, darkcolor=CANVAS, bordercolor=CANVAS)
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK,
                    borderwidth=0, relief="flat", lightcolor=BORDER, darkcolor=BORDER,
                    rowheight=32)
    style.configure("Treeview.Heading", background=SUNKEN, foreground="#65635C", padding=(8, 8), relief="flat")
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    # Compact variant for space-constrained dialogs (e.g. voucher preview in review detail)
    style.configure("Compact.Treeview", rowheight=27)
    style.map("Treeview.Heading", background=[("active", "#E9E5DC")])
    for axis in ("Vertical", "Horizontal"):
        style.configure(f"{axis}.TScrollbar", background=BORDER_STRONG, troughcolor=CANVAS,
                        borderwidth=0, arrowsize=12, relief="flat", lightcolor=CANVAS, darkcolor=CANVAS,
                        bordercolor=CANVAS)
    style.configure("TProgressbar", background=ACCENT, troughcolor=SIDEBAR, borderwidth=0)
    style.configure("Thin.Horizontal.TProgressbar", background=ACCENT, troughcolor=CANVAS,
                    borderwidth=0, thickness=3)
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("Sidebar.TLabel", background=SIDEBAR)
    style.configure("SidebarHint.TLabel", background=SIDEBAR, foreground=MUTED)
    style.configure("Nav.TButton", background=SIDEBAR, anchor="w", padding=(10, 4), focuscolor="#B5AEA0")
    style.map("Nav.TButton", background=[("selected", SELECTED), ("active", "#E8E4DB")],
              foreground=[("selected", ACCENT_INK), ("!selected", INK_SOFT)])
    style.configure("Rail.Nav.TButton", anchor="center", padding=(0, 7))
    style.configure("Status.TLabel", foreground=MUTED, padding=(18, 7))

    # --- Font tokens (derived from the base size so global 9-20 scaling works)
    base = tkfont.nametofont("TkDefaultFont")
    size = abs(base.cget("size"))
    family = base.cget("family")
    root.font_body = tkfont.Font(root=root, family=family, size=size)
    root.font_small = tkfont.Font(root=root, family=family, size=max(9, size - 1))
    root.font_title = tkfont.Font(root=root, family=family, size=size + 2, weight="bold")
    root.font_brand = tkfont.Font(root=root, family=family, size=size + 1, weight="bold")
    root.font_mono = tkfont.Font(root=root, family=_mono_family(), size=max(9, size - 1))
    root.intro_font = tkfont.Font(root=root, family=family, size=size + 3, weight="bold")
    root.welcome_font = tkfont.Font(root=root, family=family, size=size + 13)
    style.configure("Intro.TLabel", font=root.intro_font)
    style.configure("Title.TLabel", font=root.font_title)
    style.configure("Brand.TLabel", background=SIDEBAR, font=root.intro_font)
    style.configure("Welcome.TLabel", font=root.welcome_font)
    style.configure("Mono.TLabel", font=root.font_mono)
