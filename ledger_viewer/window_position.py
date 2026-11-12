"""Position detail windows relative to the current ledger window."""


def center_window(window, parent, width, height):
    parent.update_idletasks()
    # Both windows have normal title bars: use the parent's frame origin,
    # rather than its content origin, to avoid adding the title-bar height.
    x = parent.winfo_x() + (parent.winfo_width() - width) // 2
    y = parent.winfo_y() + (parent.winfo_height() - height) // 2
    left, top = parent.winfo_vrootx(), parent.winfo_vrooty()
    x = max(left, min(x, left + parent.winfo_vrootwidth() - width))
    y = max(top, min(y, top + parent.winfo_vrootheight() - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
