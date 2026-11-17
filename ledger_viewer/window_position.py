"""Screen-constrained sizing and positioning shared by detail windows."""


def constrained_size(width, height, available_width, available_height, *,
                     horizontal_margin=40, vertical_margin=60):
    """Clamp requested dimensions to the usable display area.

    Keep the helper independent of Tk so small-screen and oversized-content
    behavior can be regression-tested without creating a window.
    """
    width, height = max(1, int(width)), max(1, int(height))
    max_width = max(1, int(available_width) - max(0, int(horizontal_margin)))
    max_height = max(1, int(available_height) - max(0, int(vertical_margin)))
    return min(width, max_width), min(height, max_height)


def center_window(window, parent, width, height):
    parent.update_idletasks()
    left, top = parent.winfo_vrootx(), parent.winfo_vrooty()
    available_width = parent.winfo_vrootwidth() or parent.winfo_screenwidth()
    available_height = parent.winfo_vrootheight() or parent.winfo_screenheight()
    width, height = constrained_size(width, height, available_width, available_height)
    # Both windows have normal title bars: use the parent's frame origin,
    # rather than its content origin, to avoid adding the title-bar height.
    x = parent.winfo_x() + (parent.winfo_width() - width) // 2
    y = parent.winfo_y() + (parent.winfo_height() - height) // 2
    x = max(left, min(x, left + available_width - width))
    y = max(top, min(y, top + available_height - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
