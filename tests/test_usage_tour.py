"""Focused checks for the live spotlight tour, input, and saved completion."""
import tempfile
import time
import tkinter as tk
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.ui import Application, SettingsDialog
from ledger_viewer.usage_tour import STEPS, UsageTour, card_position


@unittest.skipUnless(os.environ.get('DUDLEDGER_VISIBLE_UI_TESTS') == '1',
                     '默认仅运行无界面回归；交互界面检查须显式启用')
class SpotlightTourTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Application()
        self.window = next(iter(self.root.windows))
        self.root.preferences_path = Path(self.temp.name) / 'preferences.json'
        self.root.preferences = {}
        self.errors = []
        self.root.report_callback_exception = lambda *exc: self.errors.append(exc)
        self.root.update()

    def tearDown(self):
        tour = getattr(self.root, '_usage_tour', None)
        if tour and not tour.closed:
            tour.destroy()
        self.window._close()
        self.temp.cleanup()
        self.assertFalse(self.errors, self.errors)

    def settle(self):
        deadline = time.monotonic() + .13
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)

    def click_mask(self, tour, x, y):
        rootx, rooty = tour.owner.winfo_rootx() + x, tour.owner.winfo_rooty() + y
        receiver = self.window.winfo_containing(rootx, rooty)
        self.assertIsNotNone(receiver)
        for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
            receiver.event_generate(event, x=rootx - receiver.winfo_rootx(), y=rooty - receiver.winfo_rooty(),
                rootx=rootx, rooty=rooty, when='tail')
            self.root.update()
        self.settle()

    def assert_layout(self, tour):
        target = tour.target
        ox, oy = self.window.winfo_rootx(), self.window.winfo_rooty()
        x, y = target.winfo_rootx() - ox, target.winfo_rooty() - oy
        self.assertEqual(tour.hole_bounds, (max(0, x - 8), max(0, y - 8),
            min(self.window.winfo_width(), x + target.winfo_width() + 8),
            min(self.window.winfo_height(), y + target.winfo_height() + 8)))
        a, b, c, d = tour.card_bounds
        self.assertEqual(c - a, 300)
        self.assertGreaterEqual(a, 10)
        self.assertGreaterEqual(b, 10)
        self.assertLessEqual(c, self.window.winfo_width() - 10)
        self.assertLessEqual(d, self.window.winfo_height() - 10)
        x1, y1, x2, y2 = tour.hole_bounds
        self.assertTrue(c <= x1 or a >= x2 or d <= y1 or b >= y2)

    def test_first_open_uses_six_small_steps_and_does_not_read_or_send_keys(self):
        with patch('ledger_viewer.ui.deepseek_get_api_key') as key, patch('ledger_viewer.ui.SettingsDialog') as settings:
            tour = self.root.offer_ai_setup()
            self.settle()
            self.assertIsInstance(tour, UsageTour)
            self.assertIs(self.root.offer_ai_setup(), tour)
            self.assertEqual(len(STEPS), 6)
            self.assertEqual(tour.card.master, self.window)
            key.assert_not_called()
            settings.assert_not_called()
            for index in range(len(STEPS)):
                tour._show_step(index)
                self.settle()
                self.assert_layout(tour)
                self.assertLess(len(tour.description.get()), 85)
            self.assertEqual(tour.next_button._text, '开始使用')

    def test_pointer_routing_advances_and_background_or_highlight_does_not(self):
        tour = self.root.offer_ai_setup()
        self.settle()
        with patch.object(self.window.import_cta, '_command') as import_file:
            self.click_mask(tour, 600, 400)
            x1, y1, x2, y2 = tour.hole_bounds
            self.click_mask(tour, (x1 + x2) // 2, (y1 + y2) // 2)
            import_file.assert_not_called()
        self.assertEqual(tour.step_index, 0)
        button = tour.next_button
        self.click_mask(tour, button.winfo_rootx() - self.window.winfo_rootx() + 8,
                        button.winfo_rooty() - self.window.winfo_rooty() + 8)
        self.assertEqual(tour.step_index, 1)
        tour.back_button.invoke()
        self.settle()
        self.assertEqual(tour.step_index, 0)
        self.assertFalse(self.root.preferences.get('onboarding_seen'))

    def test_follow_move_resize_and_tk_display_scaling(self):
        tour = self.root.offer_ai_setup()
        self.settle()
        scaling = self.root.tk.call('tk', 'scaling')
        try:
            for geometry, scale in (('820x520+130+160', 1.5), ('1160x690+80+90', scaling)):
                self.root.tk.call('tk', 'scaling', scale)
                self.window.geometry(geometry)
                self.settle()
                for index in (0, 1, 4, 5):
                    tour._show_step(index)
                    self.settle()
                    self.assert_layout(tour)
                self.assertEqual(tour.mask.winfo_rootx(), self.window.winfo_rootx())
                self.assertEqual(tour.mask.winfo_rooty(), self.window.winfo_rooty())
        finally:
            self.root.tk.call('tk', 'scaling', scaling)

    def test_complete_or_skip_persists_and_help_replays_without_duplicate(self):
        tour = self.root.offer_ai_setup()
        self.settle()
        for _ in range(len(STEPS)):
            tour.next()
            self.settle()
        self.assertTrue(tour.closed)
        self.assertTrue(self.root.preferences['onboarding_seen'])
        self.assertIn('onboarding_seen', self.root.preferences_path.read_text())
        self.assertIsNone(self.root.offer_ai_setup())
        menu = self.window.nametowidget(self.window.cget('menu'))
        help_menu = self.window.nametowidget(menu.entrycget(menu.index('帮助'), 'menu'))
        self.assertEqual(help_menu.entrycget(0, 'label'), '重新查看使用引导')
        help_menu.invoke(0)
        self.settle()
        replay = self.root._usage_tour
        self.assertIsNot(tour, replay)
        self.assertIs(self.window.show_usage_tour(), replay)
        replay.skip()
        self.assertIsNone(self.root.offer_ai_setup())
        self.assertFalse(self.window._sidebar_expanded)

    def test_closing_owner_cleans_layers_without_marking_tour_done(self):
        tour = self.root.offer_ai_setup()
        self.settle()
        tour.owner.destroy()
        self.assertTrue(tour.closed)
        self.assertFalse(self.root.preferences.get('onboarding_seen'))
        self.root.windows.discard(tour.owner)
        self.window = self.root.new_window()

    def test_existing_book_pages_and_menu_states_are_restored(self):
        self.window._use_book(Path(self.temp.name) / '演示账套.sqlite')
        self.window.select_page('entries')
        self.root.update()
        shortcut = self.window.bind('<Command-i>')
        tour = self.root.offer_ai_setup()
        self.settle()
        menu = self.window.nametowidget(self.window.cget('menu'))
        self.assertEqual(menu.entrycget(menu.index('文件'), 'state'), 'disabled')
        tour._show_step(1)
        self.settle()
        self.assertEqual(self.window.current_page(), 'accounts')
        tour._show_step(4)
        self.settle()
        self.assertEqual(self.window.current_page(), 'review')
        tour.skip()
        self.root.update()
        self.assertEqual(self.window.current_page(), 'entries')
        self.assertEqual(menu.entrycget(menu.index('文件'), 'state'), 'normal')
        self.assertEqual(self.window.bind('<Command-i>'), shortcut)


class CardPlacementTest(unittest.TestCase):
    def test_edges_and_corners_do_not_cover_target(self):
        for bounds in ((8, 8, 100, 45), (710, 8, 810, 45), (8, 460, 100, 510), (710, 460, 810, 510)):
            a, b, c, d = card_position(bounds, (300, 180), (820, 520))
            self.assertTrue(0 <= a < c <= 820 and 0 <= b < d <= 520)
            x1, y1, x2, y2 = bounds
            self.assertTrue(c <= x1 or a >= x2 or d <= y1 or b >= y2)
