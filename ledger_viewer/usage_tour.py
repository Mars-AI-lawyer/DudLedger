"""Live, window-local spotlight tour. No screenshots or customer data are used."""
from __future__ import annotations

import math
import tkinter as tk

from . import theme
from .widgets import RoundedButton, _rounded_points


STEPS = (
    ('先导入一份账簿', '选择 Excel，AI 识别前 20 行，核对后即可导入。复杂表格也能复制任务给自己的 Agent。', 'import', None),
    ('从科目开始查账', '这里按科目汇总账簿。导入后，选一个科目就能继续查看明细。', 'accounts', 'accounts'),
    ('双击，查看完整凭证', '在明细账中双击一行，即可查看完整凭证。需要跟进时，可添加关注和备注。', 'entries', 'entries'),
    ('把线索收集成问题', '在这里新建尽调问题，集中收集关联凭证和核查备注。后续跟进、整理材料更方便。', 'issues', None),
    ('让 AI 辅助筛查', '选择科目和提示词，先预览再发送。Beta 审查会向模型企业发送日期、摘要、金额等信息，请酌情使用。', 'review', 'review'),
    ('按需配置 AI 服务', '在“AI 服务”填写 DeepSeek 和百炼密钥。百炼已预填试用地址，也可按官方说明更换专属地址。', 'settings', None),
)


def card_position(bounds, size, window, gap=12):
    """Prefer a nearby free side, keep the entire card inside the client area."""
    x1, y1, x2, y2 = bounds
    width, height = size
    ww, wh = window
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    candidates = ((x2 + gap, cy - height / 2), (x1 - width - gap, cy - height / 2),
                  (cx - width / 2, y2 + gap), (cx - width / 2, y1 - height - gap))
    placed = []
    for x, y in candidates:
        x = max(gap, min(x, ww - width - gap))
        y = max(gap, min(y, wh - height - gap))
        overlap = max(0, min(x + width, x2) - max(x, x1)) * max(0, min(y + height, y2) - max(y, y1))
        placed.append((overlap, int(x), int(y)))
    _, x, y = min(enumerate(placed), key=lambda pair: (pair[1][0], pair[0]))[1]
    return x, y, x + width, y + height


def rounded_outline(bounds, radius=12):
    x1, y1, x2, y2 = bounds
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    points = []
    for cx, cy, start in ((x2 - radius, y1 + radius, -90), (x2 - radius, y2 - radius, 0),
                           (x1 + radius, y2 - radius, 90), (x1 + radius, y1 + radius, 180)):
        for i in range(9):
            angle = math.radians(start + i * 90 / 8)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


class UsageTour:
    """Native shade panels around an in-window, keyboard-accessible card.

    macOS uses its native transparent window backing; Windows uses a color key.
    Other Tk backends fall back to translucent strips around the clear regions.
    Both layers stay within the owner's client area and disappear with it.
    """
    def __init__(self, owner):
        self.owner = owner
        self.controller = owner.controller
        self.closed = False
        self.step_index = 0
        self.target = None
        self.hole_bounds = self.card_bounds = None
        self._layout_key = None
        self._poll_id = self._animation_id = None
        self._transitioning = False
        self._slide_offset = 0
        self._card_width, self._card_height = 300, 200
        self._previous_grab = owner.grab_current()
        self._previous_focus = owner.focus_get()
        self._original_sidebar = owner._sidebar_expanded
        self._original_page = owner.current_page()
        self._fallback = []
        self.mask, self.transparent = self._layer()
        self.mask.attributes('-alpha', .58)
        self.canvas = tk.Canvas(self.mask, background=self.transparent or theme.CANVAS,
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill='both', expand=True)
        self._mask_sections = [(self.mask, self.canvas)]
        # The card has no native shade window above it. Match the shaded
        # workspace at its rounded corners, which lie outside the white card.
        shade = '#18221E'
        card_background = '#' + ''.join(f'{round(int(theme.CANVAS[i:i+2], 16) * .42 + int(shade[i:i+2], 16) * .58):02x}'
                                        for i in (1, 3, 5))
        self.card = tk.Canvas(owner, background=card_background, highlightthickness=0, borderwidth=0)
        self.card_canvas = self.card
        self.body = tk.Frame(self.card_canvas, background=theme.SURFACE)
        self.card_canvas.create_window(18, 16, anchor='nw', window=self.body, width=264)
        # Share the application's live derived fonts so font-size changes also
        # remeasure an open tour instead of leaving fixed 12/11/10px copies.
        self.title_font = self.controller.font_title
        self.text_font = self.controller.font_body
        self.small_font = self.controller.font_small
        self.title = tk.StringVar()
        self.description = tk.StringVar()
        self.progress = tk.StringVar()
        tk.Label(self.body, textvariable=self.title, font=self.title_font, bg=theme.SURFACE,
                 fg=theme.INK, anchor='w', justify='left', wraplength=264).pack(fill='x')
        tk.Label(self.body, textvariable=self.description, font=self.text_font, bg=theme.SURFACE,
                 fg=theme.INK_SOFT, anchor='w', justify='left', wraplength=264).pack(fill='x', pady=(10, 18))
        footer = tk.Frame(self.body, background=theme.SURFACE)
        footer.pack(fill='x')
        tk.Label(footer, textvariable=self.progress, font=self.small_font, bg=theme.SURFACE,
                 fg=theme.MUTED).pack(side='left')
        self.next_button = RoundedButton(footer, text='下一步', command=self.next, font=self.small_font,
                                          padx=12, pady=7)
        self.next_button.pack(side='right')
        self.skip_button = self._link(footer, '跳过', self.skip)
        self.skip_button.pack(side='right', padx=(4, 8))
        self.back_button = self._link(footer, '上一步', self.back)
        self.back_button.pack(side='right')
        self.mask.bind('<Escape>', lambda _event: self.skip())
        self.mask.protocol('WM_DELETE_WINDOW', self.skip)
        self._escape_binding = owner.bind('<Escape>', lambda _event: self.skip(), add='+')
        self._pointer_button = None
        for surface in (self.canvas, self.card):
            self._bind_surface(surface)
        self._configure_binding = owner.bind('<Configure>', self._configured, add='+')
        self._destroy_binding = owner.bind('<Destroy>', self._owner_destroyed, add='+')
        self._freeze_actions()
        owner._sidebar_expanded = True
        owner._resize_workspace()
        self._show_step(0)
        self._poll()

    def _layer(self):
        layer = tk.Toplevel(self.owner)
        layer.withdraw()
        layer.overrideredirect(True)
        layer.transient(self.owner)
        system = self.owner.tk.call('tk', 'windowingsystem')
        transparent = None
        if system == 'aqua':
            layer.attributes('-transparent', True)
            transparent = 'systemTransparent'
        elif system == 'win32':
            transparent = '#ff00fe'
            layer.attributes('-transparentcolor', transparent)
        layer.configure(background=transparent or theme.CANVAS)
        return layer, transparent

    def _bind_surface(self, surface):
        surface.bind('<ButtonPress-1>', lambda event: self._pointer(event, True))
        surface.bind('<ButtonRelease-1>', lambda event: self._pointer(event, False))
        surface.bind('<Motion>', self._pointer_hover)
        surface.bind('<MouseWheel>', lambda _event: 'break')

    def _new_mask_section(self):
        layer, transparent = self._layer()
        layer.attributes('-alpha', .58)
        layer.bind('<Escape>', lambda _event: self.skip())
        layer.protocol('WM_DELETE_WINDOW', self.skip)
        canvas = tk.Canvas(layer, background=transparent or theme.CANVAS,
                           highlightthickness=0, borderwidth=0)
        canvas.pack(fill='both', expand=True)
        self._bind_surface(canvas)
        return layer, canvas

    def _freeze_actions(self):
        self._shortcuts = {}
        for sequence in ('<Command-i>', '<Command-o>', '<Control-i>', '<Control-o>', '<Command-Shift-L>', '<Control-Shift-L>'):
            self._shortcuts[sequence] = self.owner.bind(sequence)
            self.owner.bind(sequence, lambda _event: 'break')
        self._menu_states = []
        menu_name = self.owner.cget('menu')
        if menu_name:
            menu = self.owner.nametowidget(menu_name)
            for index in range((menu.index('end') or 0) + 1):
                if menu.type(index) == 'cascade' and menu.entrycget(index, 'label') != '帮助':
                    self._menu_states.append((menu, index, menu.entrycget(index, 'state')))
                    menu.entryconfigure(index, state='disabled')

    def _link(self, parent, text, command):
        return RoundedButton(parent, text=text, command=command, variant='ghost',
                             font=self.small_font, padx=4, pady=7)

    def _button_at(self, event):
        for button in (self.next_button, self.back_button, self.skip_button):
            if not button.winfo_ismapped():
                continue
            x, y = button.winfo_rootx(), button.winfo_rooty()
            if x <= event.x_root < x + button.winfo_width() and y <= event.y_root < y + button.winfo_height():
                return button
        return None

    def _pointer(self, event, pressed):
        # Transparent native windows may receive mouse events in their clear
        # pixels. Route only card controls, never the highlighted real control.
        button = self._button_at(event)
        if pressed:
            self._pointer_button = button
        else:
            previous, self._pointer_button = self._pointer_button, None
            if button is not None and button is previous:
                button.invoke()
        return 'break'

    def _pointer_hover(self, event):
        self.canvas.configure(cursor='hand2' if self._button_at(event) else 'arrow')
        for button in (self.next_button, self.back_button, self.skip_button):
            button._redraw()

    def winfo_exists(self):
        return not self.closed

    def lift(self):
        if not self.closed:
            if self.transparent:
                for layer, _canvas in self._mask_sections:
                    if layer.state() != 'withdrawn':
                        layer.lift(self.owner)
            self.card.tk.call('raise', str(self.card))

    def _configured(self, event):
        if event.widget is self.owner and not self.closed:
            self._follow()

    def _owner_destroyed(self, event):
        if event.widget is self.owner:
            self.close(persist=False, restore=False)

    def _poll(self):
        if self.closed:
            return
        self._follow()
        self._poll_id = self.owner.after(60, self._poll)

    def _show_step(self, index):
        if self.closed:
            return
        self.step_index = index
        title, description, target, page = STEPS[index]
        if self.owner.store and page:
            self.owner.select_page(page)
        self.target = {'import': self.owner.import_cta, 'accounts': self.owner.nav_buttons[0],
            'entries': self.owner.nav_buttons[1], 'issues': self.owner.workbench_button,
            'review': self.owner.nav_buttons[4], 'settings': self.owner.settings_button}[target]
        self.title.set(title)
        self.description.set(description)
        self.progress.set(f'{index + 1} / {len(STEPS)}')
        self.back_button.configure(state='normal' if index else 'disabled')
        if index:
            self.back_button.pack(side='right')
        else:
            self.back_button.pack_forget()
        self.next_button.configure(text='开始使用' if index == len(STEPS) - 1 else '下一步', state='normal')
        self.owner.update_idletasks()
        self._card_width = 300
        self._card_height = self.body.winfo_reqheight() + 36
        self._layout_key = None
        self._follow()
        self.card.grab_set()
        self.next_button.focus_force()
        self._slide()

    def _slide(self):
        if self._animation_id is not None:
            self.owner.after_cancel(self._animation_id)
        self._transitioning = True
        def frame(number=0):
            if self.closed:
                return
            self._slide_offset = 3 - number
            self._layout_key = None
            self._follow()
            if number < 3:
                self._animation_id = self.owner.after(24, lambda: frame(number + 1))
            else:
                self._animation_id = None
                self._transitioning = False
        frame()

    def _go(self, index):
        if self.closed or self._transitioning or index == self.step_index:
            return
        self._show_step(index)

    def next(self):
        if self._transitioning or self.closed:
            return
        if self.step_index == len(STEPS) - 1:
            self.close()
        else:
            self._go(self.step_index + 1)

    def back(self):
        if self.step_index:
            self._go(self.step_index - 1)

    def skip(self):
        self.close()

    def _follow(self):
        if self.closed or self.target is None:
            return
        if not self.owner.winfo_viewable():
            for layer, _canvas in self._mask_sections:
                layer.withdraw()
            self.card.place_forget()
            for strip in self._fallback:
                strip.withdraw()
            self._layout_key = None
            return
        if not self.target.winfo_ismapped():
            self.owner._sidebar_expanded = True
            self.owner._resize_workspace()
            return
        ox, oy = self.owner.winfo_rootx(), self.owner.winfo_rooty()
        ww, wh = self.owner.winfo_width(), self.owner.winfo_height()
        x, y = self.target.winfo_rootx() - ox, self.target.winfo_rooty() - oy
        bounds = (max(0, x - 8), max(0, y - 8), min(ww, x + self.target.winfo_width() + 8),
                  min(wh, y + self.target.winfo_height() + 8))
        scaling = self.owner.tk.call('tk', 'scaling')
        self._card_height = self.body.winfo_reqheight() + 36
        key = (ox, oy, ww, wh, bounds, scaling, self._card_height, self._slide_offset)
        if key == self._layout_key:
            return
        self._layout_key = key
        self.hole_bounds = bounds
        self.card_bounds = card_position(bounds, (self._card_width, self._card_height), (ww, wh))
        x1, y1, x2, y2 = self.card_bounds
        offset = min(self._slide_offset, max(0, wh - 10 - y2))
        self.card_bounds = (x1, y1 + offset, x2, y2 + offset)
        self.card.place(x=x1, y=y1 + offset, width=x2 - x1, height=y2 - y1)
        points = rounded_outline(bounds)
        if self.transparent:
            # Transparent native pixels are not reliably mouse-transparent.
            # Physically exclude the entire card rectangle from every window,
            # so its buttons receive native press/release events directly.
            contour = [0, 0, ww, 0, ww, wh, 0, wh, 0, 0]
            pairs = list(zip(points[::2], points[1::2]))
            inner = [coordinate for pair in reversed(pairs) for coordinate in pair]
            contour += points[:2] + inner + points[:2] + [0, 0]
            self._draw_mask_sections(contour, points, (ox, oy, ww, wh))
        else:
            self._draw_fallback(bounds, (ox, oy, ww, wh))
        self.card_canvas.delete('shape')
        self.card_canvas.create_polygon(_rounded_points(2, 4, self._card_width - 1, self._card_height - 1, 13),
                                        smooth=True, fill='#68716A', outline='', tags='shape')
        self.card_canvas.create_polygon(_rounded_points(0, 0, self._card_width - 4, self._card_height - 5, 12),
                                        smooth=True, fill=theme.SURFACE, outline=theme.BORDER, tags='shape')
        self.card_canvas.tag_lower('shape')
        self.lift()

    def _draw_mask_sections(self, contour, outline, window):
        ox, oy, ww, wh = window
        a, b, c, d = self.card_bounds
        rectangles = ((0, 0, ww, b), (0, d, ww, wh), (0, b, a, d), (c, b, ww, d))
        while len(self._mask_sections) < len(rectangles):
            self._mask_sections.append(self._new_mask_section())
        for (layer, canvas), (left, top, right, bottom) in zip(self._mask_sections, rectangles):
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                layer.withdraw()
                continue
            layer.geometry(f'{width}x{height}+{ox + left}+{oy + top}')
            canvas.delete('all')
            def local(points):
                return [value - (left if index % 2 == 0 else top) for index, value in enumerate(points)]
            canvas.create_polygon(local(contour), fill='#18221E', outline='')
            canvas.create_polygon(local(outline), fill='', outline='#E6ECE4', width=1)
            layer.deiconify()

    def _draw_fallback(self, bounds, window):
        x1, y1, x2, y2 = bounds
        ox, oy, ww, wh = window
        rectangles = []
        a, b, c, d = self.card_bounds
        for left, top, right, bottom in ((0, 0, ww, y1), (0, y2, ww, wh), (0, y1, x1, y2), (x2, y1, ww, y2)):
            ix1, iy1, ix2, iy2 = max(left, a), max(top, b), min(right, c), min(bottom, d)
            if ix1 < ix2 and iy1 < iy2:
                rectangles.extend(((left, top, right, iy1), (left, iy2, right, bottom),
                                   (left, iy1, ix1, iy2), (ix2, iy1, right, iy2)))
            else:
                rectangles.append((left, top, right, bottom))
        while len(self._fallback) < len(rectangles):
            strip, _ = self._layer()
            strip.configure(bg='#18221E')
            strip.attributes('-alpha', .58)
            self._fallback.append(strip)
        for index, strip in enumerate(self._fallback):
            if index >= len(rectangles):
                strip.withdraw()
                continue
            x, y, right, bottom = rectangles[index]
            w, h = right - x, bottom - y
            if w > 0 and h > 0:
                strip.geometry(f'{w}x{h}+{ox + x}+{oy + y}')
                strip.deiconify()
                strip.lift(self.owner)
            else:
                strip.withdraw()

    def close(self, *, persist=True, restore=True):
        if self.closed:
            return
        self.closed = True
        for timer in (self._poll_id, self._animation_id):
            if timer is not None:
                self.owner.after_cancel(timer)
        self.owner.unbind('<Configure>', self._configure_binding)
        self.owner.unbind('<Destroy>', self._destroy_binding)
        self.owner.unbind('<Escape>', self._escape_binding)
        try:
            self.card.grab_release()
        except tk.TclError:
            pass
        for layer in (self.card, *(layer for layer, _canvas in self._mask_sections), *self._fallback):
            try:
                layer.destroy()
            except tk.TclError:
                pass
        if restore and self.owner.winfo_exists():
            for sequence, script in self._shortcuts.items():
                self.owner.bind(sequence, script or '')
            for menu, index, state in self._menu_states:
                menu.entryconfigure(index, state=state)
            self.owner._sidebar_expanded = self._original_sidebar
            if self._original_page:
                self.owner.select_page(self._original_page)
            self.owner._resize_workspace()
            if self._previous_grab is not None and self._previous_grab.winfo_exists():
                self._previous_grab.grab_set()
            if self._previous_focus is not None and self._previous_focus.winfo_exists():
                self._previous_focus.focus_set()
        if persist:
            self.controller.preferences['onboarding_seen'] = True
            self.controller._save_preferences()

    def destroy(self):
        self.close(persist=False)
