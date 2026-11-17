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
ACCENT_SOFT = "#F1E3DD"     # terracotta tint for navigation/badges, not table states

# Text layers
INK = "#30302C"             # primary text
INK_SOFT = "#53524C"        # secondary text
MUTED = "#6B6962"           # hint text; >= 4.5:1 on Canvas and Sidebar
DISABLED = "#77746C"        # readable disabled text, with a recessed fill

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
FOCUS_RING = "#315F78"      # keyboard focus, deliberately cooler than hover

# Table states are separate channels. In particular, a native selected row,
# a checked voucher, a followed voucher and a rectangular cell range must not
# share Accent Soft as a catch-all background.
TABLE_SELECTED = "#E7ECEF"
TABLE_SELECTED_INK = INK
TABLE_CHECKED = "#E8F0EA"
TABLE_CHECKED_INK = "#2F5940"
TABLE_MARKED = "#F7ECDD"
TABLE_MARKED_INK = ACCENT_INK
TABLE_ERROR = DANGER_SOFT
TABLE_ERROR_INK = DANGER
TABLE_PENDING = "#F2F4F5"
RANGE_BORDER = ACCENT
DEBIT_INK = "#355E70"
CREDIT_INK = "#776B58"

# ---------------------------------------------------------------------------
# Radius & spacing tokens (used by widgets.py canvas-drawn components)
# ---------------------------------------------------------------------------

RADIUS_CONTROL = 4          # ordinary buttons and other compact controls
RADIUS_POPUP = 6            # menus and small floating surfaces
RADIUS_PANEL = 12           # existing large panels/cards only

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


def treeview_row_height(font, *, compact=False):
    """Return a density row height from real font metrics, never font size alone.

    The padding preserves the existing 32/27px baseline for a 16px-linespace
    font while allowing large fonts and CJK fallback fonts to grow naturally.
    """
    linespace = max(1, int(font.metrics("linespace")))
    return max(24 if compact else 30, linespace + (11 if compact else 16))


def configure_derived_fonts(root, size=None):
    """Create or reconfigure every shared derived font as one operation."""
    base = tkfont.nametofont("TkDefaultFont")
    size = abs(base.cget("size")) if size is None else int(size)
    family = base.cget("family")
    specifications = {
        "font_body": (family, size, "normal"),
        "font_small": (family, max(9, size - 1), "normal"),
        "font_title": (family, size + 2, "bold"),
        "font_brand": (family, size + 1, "bold"),
        "font_mono": (family, size, "normal"),
        "intro_font": (family, size + 3, "bold"),
        "welcome_font": (family, size + 13, "normal"),
    }
    fonts = {}
    for name, (font_family, font_size, weight) in specifications.items():
        font = getattr(root, name, None)
        if font is None:
            font = tkfont.Font(root=root, family=font_family, size=font_size, weight=weight)
            setattr(root, name, font)
        else:
            font.configure(family=font_family, size=font_size, weight=weight)
        fonts[name] = font
    return fonts


def configure_treeview_metrics(root):
    """Synchronize normal/compact table density with the current data font."""
    style = ttk.Style(root)
    data_font = getattr(root, "font_body", None) or tkfont.nametofont("TkDefaultFont")
    # Keep table numbers and text in the original system UI family.
    metric_fonts = [data_font]
    body_font = getattr(root, "font_body", None)
    if body_font is not None and body_font is not data_font:
        metric_fonts.append(body_font)  # include actual CJK/system-font line height
    style.configure("Treeview", font=data_font,
                    rowheight=max(treeview_row_height(font) for font in metric_fonts))
    style.configure("Compact.Treeview", font=data_font,
                    rowheight=max(treeview_row_height(font, compact=True) for font in metric_fonts))
    for name in ("Amount.TEntry", "Amount.TLabel", "Amount.Treeview"):
        style.configure(name, font=data_font)


def configure_table_semantics(view):
    """Apply shared, independent row-state tags without changing row data."""
    tags = {
        "selected": dict(background=TABLE_SELECTED, foreground=TABLE_SELECTED_INK),
        "checked": dict(background=TABLE_CHECKED, foreground=TABLE_CHECKED_INK),
        "voucher_checked": dict(background=TABLE_CHECKED, foreground=TABLE_CHECKED_INK),
        "marked": dict(background=TABLE_MARKED, foreground=TABLE_MARKED_INK),
        "difference": dict(background=TABLE_ERROR, foreground=TABLE_ERROR_INK),
        "error": dict(background=TABLE_ERROR, foreground=TABLE_ERROR_INK),
        "pending": dict(background=TABLE_PENDING, foreground=INK_SOFT),
        "debit_row": dict(foreground=DEBIT_INK),
        "credit_row": dict(foreground=CREDIT_INK),
        "account_parent": dict(foreground=INK_SOFT),
    }
    for tag, options in tags.items():
        view.tag_configure(tag, **options)


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
                    lightcolor=CANVAS, darkcolor=BORDER, focuscolor=FOCUS_RING)
    for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
        style.configure(name, background=CANVAS)
    style.configure("Hint.TLabel", foreground=MUTED)

    # --- Button hierarchy -------------------------------------------------
    # Secondary (default TButton): white surface + thin outline
    style.configure("TButton", padding=PAD_BUTTON, background=SURFACE, foreground=INK,
                    borderwidth=1, relief="solid", bordercolor=BORDER_STRONG,
                    lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                    focusthickness=1, focuscolor=FOCUS_RING)
    style.map("TButton",
              background=[("disabled", SUNKEN), ("pressed", SELECTED), ("active", HOVER)],
              foreground=[("disabled", DISABLED)],
              bordercolor=[("focus", FOCUS_RING)])
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
                    foreground=INK_SOFT, borderwidth=0, relief="flat", focuscolor=FOCUS_RING)
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
    style.map("TEntry", bordercolor=[("focus", FOCUS_RING)])
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
    style.layout('TCheckbutton', style.layout('Sampling.TCheckbutton'))
    style.configure('Segment.TRadiobutton', padding=(14, 7), background=SIDEBAR)
    style.map('Segment.TRadiobutton', background=[('selected', ACCENT_SOFT), ('active', '#E8E4DB')], foreground=[('selected', ACCENT_INK)])
    style.configure("TSeparator", background=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TNotebook", background=CANVAS, borderwidth=0)
    style.configure("TNotebook.Tab", background=SIDEBAR, padding=(12, 7))
    style.map("TNotebook.Tab", background=[("selected", CANVAS)])
    style.layout("Workspace.TNotebook.Tab", [])
    style.configure("Workspace.TNotebook", tabmargins=0, borderwidth=0, lightcolor=CANVAS, darkcolor=CANVAS, bordercolor=CANVAS)
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK,
                    borderwidth=0, relief="flat", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Treeview.Heading", background=SUNKEN, foreground=INK_SOFT, padding=(8, 8), relief="flat")
    style.map("Treeview", background=[("selected", TABLE_SELECTED)],
              foreground=[("selected", TABLE_SELECTED_INK)])
    # Compact variant shares the same font and derives its height below.
    style.configure("Compact.Treeview")
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
    style.configure("Nav.TButton", background=SIDEBAR, anchor="w", padding=(10, 4), focuscolor=FOCUS_RING)
    style.map("Nav.TButton", background=[("selected", SELECTED), ("active", "#E8E4DB")],
              foreground=[("selected", ACCENT_INK), ("!selected", INK_SOFT)])
    style.configure("Tool.Nav.TButton", background=SIDEBAR, anchor="w", padding=(14, 3),
                    foreground=MUTED, focuscolor=FOCUS_RING)
    style.map("Tool.Nav.TButton", background=[("selected", SELECTED), ("active", HOVER)],
              foreground=[("selected", ACCENT_INK), ("!selected", MUTED)])
    style.configure("Rail.Nav.TButton", anchor="center", padding=(0, 4))
    style.configure("Rail.Tool.Nav.TButton", anchor="center", padding=(0, 3), foreground=MUTED)
    style.configure("Icon.TButton", padding=4)  # 20px icon + padding = >= 28px target
    style.configure("Status.TLabel", foreground=MUTED, padding=(18, 7))
    style.configure("TableFeedback.TLabel", foreground=INK_SOFT, padding=(5, 3))
    style.configure("TableFeedback.Warning.TLabel", foreground=WARNING, padding=(5, 3))

    # --- Font tokens (all recreated/reconfigured through one public helper)
    configure_derived_fonts(root)
    style.configure("Intro.TLabel", font=root.intro_font)
    style.configure("Title.TLabel", font=root.font_title)
    style.configure("Brand.TLabel", background=SIDEBAR, font=root.intro_font)
    style.configure("Welcome.TLabel", font=root.welcome_font)
    style.configure("Mono.TLabel", font=root.font_mono)
    configure_treeview_metrics(root)
