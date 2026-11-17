"""Display-free regression checks for native overlay hit testing.

Transparent pixels do not imply click-through native windows. These checks
dispatch by window bounds before exercising the real button press/release path.
They deliberately do not inject clicks directly into an overlay callback.
"""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ledger_viewer.usage_tour import UsageTour, STEPS
from ledger_viewer.widgets import RoundedButton


class NativeLayer:
    def __init__(self):
        self.bounds = None
        self.visible = False

    def geometry(self, value):
        w, h, x, y = map(int, re.fullmatch(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', value).groups())
        self.bounds = x, y, x + w, y + h

    def deiconify(self):
        self.visible = True

    def withdraw(self):
        self.visible = False

    def state(self):
        return 'normal' if self.visible else 'withdrawn'

    def destroy(self):
        self.visible = False

    def __getattr__(self, name):
        return Mock()


def target(owner, x, y, width=160, height=32):
    return SimpleNamespace(winfo_ismapped=lambda: True,
        winfo_rootx=lambda: owner.x + x, winfo_rooty=lambda: owner.y + y,
        winfo_width=lambda: width, winfo_height=lambda: height)


def fixture(width=1160, height=690, transparent='systemTransparent'):
    tour = UsageTour.__new__(UsageTour)
    owner = Mock()
    owner.x, owner.y, owner.width, owner.height = 80, 112, width, height
    owner.winfo_viewable.return_value = True
    owner.winfo_rootx.side_effect = lambda: owner.x
    owner.winfo_rooty.side_effect = lambda: owner.y
    owner.winfo_width.side_effect = lambda: owner.width
    owner.winfo_height.side_effect = lambda: owner.height
    owner.tk.call.return_value = 1.33
    owner.store = None
    owner.import_cta = target(owner, width - 100, 17, 84)
    owner.nav_buttons = [target(owner, 9, 100 + index * 48) for index in range(5)]
    owner.workbench_button = target(owner, 9, 320)
    owner.settings_button = target(owner, 9, height - 55, 72)
    tour.owner = owner
    tour.controller = Mock(preferences={})
    tour.closed = False
    tour.step_index = 0
    tour._transitioning = False
    tour._pointer_button = None
    tour._slide_offset = 0
    tour._card_width, tour._card_height = 300, 145
    tour._layout_key = tour._poll_id = tour._animation_id = None
    tour.target = owner.import_cta
    tour._fallback = []
    tour.mask, tour.canvas = NativeLayer(), Mock()
    tour._mask_sections = [(tour.mask, tour.canvas)]
    tour.transparent = transparent
    tour.card = tour.card_canvas = Mock()
    tour.body = Mock()
    tour.body.winfo_reqheight.return_value = 109
    tour.title, tour.description, tour.progress = Mock(), Mock(), Mock()
    tour._configure_binding = tour._destroy_binding = tour._escape_binding = 'test'
    tour._shortcuts, tour._menu_states = {}, []
    tour._previous_focus = tour._previous_grab = None
    tour._original_sidebar, tour._original_page = False, None
    tour._layer = lambda: (NativeLayer(), transparent)
    tour._new_mask_section = lambda: (NativeLayer(), Mock())
    pending = []
    owner.after.side_effect = lambda _ms, fn: (pending.append(fn), len(pending))[1]
    owner.after_cancel.side_effect = lambda _timer: None
    def settle():
        while pending and not tour.closed:
            pending.pop(0)()
    tour.settle = settle
    buttons = []
    for index, callback in enumerate((tour.next, tour.back, tour.skip)):
        button = RoundedButton.__new__(RoundedButton)
        button._state, button._pressed, button._command = 'normal', False, callback
        button.focus_set, button.focus_force, button._redraw = Mock(), Mock(), Mock()
        button.configure, button.pack, button.pack_forget = Mock(), Mock(), Mock()
        button.winfo_width, button.winfo_height = lambda: 62, lambda: 26
        button.winfo_ismapped = lambda: True
        button.winfo_rootx = lambda i=index: owner.x + tour.card_bounds[2] - 80 - i * 65
        button.winfo_rooty = lambda: owner.y + tour.card_bounds[3] - 42
        buttons.append(button)
    tour.next_button, tour.back_button, tour.skip_button = buttons
    tour._follow()
    return tour


def contains(bounds, x, y):
    a, b, c, d = bounds
    return a <= x < c and b <= y < d


def native_click(tour, button):
    x, y = button.winfo_rootx() + 20, button.winfo_rooty() + 12
    # A native mask window would intercept this before Tk sees the button.
    layers = [layer for layer, _canvas in tour._mask_sections] + tour._fallback
    if any(layer.visible and contains(layer.bounds, x, y) for layer in layers):
        return False
    event = SimpleNamespace(x=20, y=12, x_root=x, y_root=y)
    button._on_press(event)
    button._on_release(event)
    tour.settle()
    return True


class SpotlightInputRegressionTest(unittest.TestCase):
    def test_first_step_next_receives_click_before_any_keyboard_input(self):
        tour = fixture()
        self.assertTrue(native_click(tour, tour.next_button), '遮罩窗口挡住了第一步的下一步按钮')
        self.assertEqual(tour.step_index, 1)

    def test_first_step_skip_receives_click_and_saves_completion(self):
        tour = fixture()
        self.assertTrue(native_click(tour, tour.skip_button), '遮罩窗口挡住了第一步的跳过按钮')
        self.assertTrue(tour.closed)
        self.assertTrue(tour.controller.preferences['onboarding_seen'])

    def test_all_steps_back_and_finish_use_button_mouse_events(self):
        tour = fixture()
        for index in range(1, len(STEPS)):
            self.assertTrue(native_click(tour, tour.next_button))
            self.assertEqual(tour.step_index, index)
        self.assertTrue(native_click(tour, tour.back_button))
        self.assertEqual(tour.step_index, len(STEPS) - 2)
        self.assertTrue(native_click(tour, tour.next_button))
        self.assertTrue(native_click(tour, tour.next_button))
        self.assertTrue(tour.closed)

    def test_no_mask_window_covers_the_card_after_resize_or_move(self):
        for transparent in ('systemTransparent', '#ff00fe', None):
            for width, height in ((820, 520), (1160, 690)):
                with self.subTest(transparent=transparent, size=(width, height)):
                    tour = fixture(width, height, transparent)
                    tour.owner.x, tour.owner.y = 140, 180
                    for index in range(len(STEPS)):
                        tour._show_step(index)
                        tour.settle()
                        a, b, c, d = tour.card_bounds
                        card = (a + tour.owner.x, b + tour.owner.y, c + tour.owner.x, d + tour.owner.y)
                        layers = [layer for layer, _canvas in tour._mask_sections] + tour._fallback
                        for layer in layers:
                            if layer.visible:
                                x1, y1, x2, y2 = layer.bounds
                                self.assertTrue(x2 <= card[0] or x1 >= card[2] or y2 <= card[1] or y1 >= card[3],
                                                '视觉透明仍不能让原生遮罩覆盖提示卡')

    def test_background_and_highlight_do_not_advance_or_close(self):
        tour = fixture()
        for x, y in ((600, 400), (tour.owner.width - 60, 30)):
            event = SimpleNamespace(x_root=tour.owner.x + x, y_root=tour.owner.y + y)
            tour._pointer(event, True)
            tour._pointer(event, False)
        self.assertEqual(tour.step_index, 0)
        self.assertFalse(tour.closed)
        tour.controller._save_preferences.assert_not_called()

    def test_panels_still_cover_the_workspace_outside_the_card(self):
        tour = fixture()
        layers = [layer for layer, _canvas in tour._mask_sections if layer.visible]
        a, b, c, d = tour.card_bounds
        self.assertEqual(sum((layer.bounds[2] - layer.bounds[0]) * (layer.bounds[3] - layer.bounds[1]) for layer in layers),
            tour.owner.width * tour.owner.height - (c - a) * (d - b))
