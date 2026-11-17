"""Display-free checks for shared design tokens and component behavior."""
import tkinter as tk
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer import theme
from ledger_viewer.navigation import (
    NavigationTooltip, TOOLTIP_DELAY_MS, icon_button_padding,
)
from ledger_viewer.table_tools import (
    SelectionSummary, TableTools, amount_kind, selection_composition,
)
from ledger_viewer.widgets import RoundedButton
from ledger_viewer.window_position import constrained_size


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
              for value in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + .05) / (low + .05)


class FakeFont:
    def __init__(self, linespace=16):
        self.linespace = linespace

    def metrics(self, name):
        assert name == "linespace"
        return self.linespace

    def measure(self, text):
        return len(text) * 8


class FakeTooltipWidget:
    def __init__(self):
        self.bindings = {}

    def bind(self, sequence, callback, add=None):
        assert add == "+"
        self.bindings[sequence] = callback


class DesignTokenTest(unittest.TestCase):
    def test_muted_matches_report_and_meets_small_text_contrast(self):
        self.assertEqual(theme.MUTED, "#6B6962")
        self.assertGreaterEqual(contrast(theme.MUTED, theme.CANVAS), 4.5)
        self.assertGreaterEqual(contrast(theme.MUTED, theme.SIDEBAR), 4.5)

    def test_table_states_and_focus_have_independent_tokens(self):
        states = {
            theme.TABLE_SELECTED, theme.TABLE_CHECKED, theme.TABLE_MARKED,
            theme.TABLE_ERROR, theme.RANGE_BORDER,
        }
        self.assertEqual(len(states), 5)
        self.assertNotEqual(theme.FOCUS_RING, theme.HOVER)
        self.assertEqual(theme.RADIUS_CONTROL, 4)

    def test_treeview_height_uses_metrics_and_grows_for_large_fonts(self):
        self.assertEqual(theme.treeview_row_height(FakeFont(16)), 32)
        self.assertEqual(theme.treeview_row_height(FakeFont(16), compact=True), 27)
        self.assertEqual(theme.treeview_row_height(FakeFont(25)), 41)
        self.assertEqual(theme.treeview_row_height(FakeFont(25), compact=True), 36)
        self.assertGreater(theme.treeview_row_height(FakeFont(40)),
                           theme.treeview_row_height(FakeFont(25)))

    def test_all_shared_derived_fonts_can_be_reconfigured(self):
        names = ("font_body", "font_small", "font_title", "font_brand",
                 "font_mono", "intro_font", "welcome_font")
        root = SimpleNamespace(**{name: Mock() for name in names})
        root.font_mono.cget.return_value = "Menlo"
        base = Mock()
        base.cget.side_effect = lambda key: 13 if key == "size" else "PingFang SC"
        with patch.object(theme.tkfont, "nametofont", return_value=base):
            fonts = theme.configure_derived_fonts(root, 20)
        self.assertEqual(set(fonts), set(names))
        root.font_body.configure.assert_called_once_with(family="PingFang SC", size=20, weight="normal")
        root.font_title.configure.assert_called_once_with(family="PingFang SC", size=22, weight="bold")
        root.font_mono.configure.assert_called_once_with(family="PingFang SC", size=20, weight="normal")
        root.font_small.configure.assert_called_once_with(family="PingFang SC", size=19, weight="normal")
        root.welcome_font.configure.assert_called_once_with(family="PingFang SC", size=33, weight="normal")

    def test_treeview_metrics_also_account_for_cjk_body_font(self):
        root = SimpleNamespace(font_mono=FakeFont(16), font_body=FakeFont(25))
        style = Mock()
        with patch.object(theme.ttk, "Style", return_value=style):
            theme.configure_treeview_metrics(root)
        normal = next(call for call in style.configure.call_args_list
                      if call.args and call.args[0] == "Treeview")
        compact = next(call for call in style.configure.call_args_list
                       if call.args and call.args[0] == "Compact.Treeview")
        self.assertEqual(normal.kwargs["rowheight"], 41)
        self.assertEqual(compact.kwargs["rowheight"], 36)
        self.assertIs(normal.kwargs['font'], root.font_body)


class SelectionCompositionTest(unittest.TestCase):
    def test_mixed_debit_and_credit_are_grouped_without_netting(self):
        composition = selection_composition([
            ("debit", "借方", Decimal("0.10"), "CNY"),
            ("credit", "贷方", Decimal("0.20"), "CNY"),
        ])
        self.assertTrue(composition.mixed_directions)
        self.assertFalse(composition.mixed_basis)
        self.assertEqual([group.total for group in composition.groups],
                         [Decimal("0.10"), Decimal("0.20")])
        self.assertIn("不是借贷净额", composition.warning_text)
        self.assertIn("借方 0.10", composition.text)
        self.assertIn("贷方 0.20", composition.text)

    def test_original_base_and_multiple_currencies_have_explicit_warnings(self):
        composition = selection_composition([
            ("original", "原币金额", Decimal("10"), "USD"),
            ("original", "原币金额", Decimal("5"), "EUR"),
            ("debit", "借方", Decimal("20"), "CNY"),
        ])
        self.assertTrue(composition.mixed_basis)
        self.assertTrue(composition.multi_currency)
        self.assertEqual(composition.currencies, ("USD", "EUR"))
        self.assertIn("未换算", composition.warning_text)
        self.assertIn("未折算", composition.warning_text)

    def test_base_currency_debit_and_credit_ignore_row_original_currencies(self):
        composition = selection_composition([
            ("debit", "借方", Decimal("10"), "USD"),
            ("debit", "借方", Decimal("20"), "EUR"),
            ("credit", "贷方", Decimal("5"), "GBP"),
        ])
        self.assertTrue(composition.mixed_directions)
        self.assertFalse(composition.mixed_basis)
        self.assertFalse(composition.multi_currency)
        self.assertEqual(composition.currencies, ())
        self.assertNotIn("未折算", composition.warning_text)
        self.assertIn("不是借贷净额", composition.warning_text)

    def test_kind_classification_does_not_confuse_opening_credit_with_original(self):
        self.assertEqual(amount_kind("oc", "期初贷方"), "credit")
        self.assertEqual(amount_kind("original", "原币金额"), "original")
        self.assertEqual(amount_kind("left_a", "金额 1"), "amount")


class NavigationAndWindowTest(unittest.TestCase):
    def test_tooltip_uses_short_delay_and_keyboard_focus_bindings(self):
        owner = Mock()
        owner.after.return_value = "pending"
        tooltip = NavigationTooltip(owner)
        widget = FakeTooltipWidget()
        tooltip.attach(widget, "明细账")
        self.assertIn("<FocusIn>", widget.bindings)
        self.assertIn("<FocusOut>", widget.bindings)
        widget.bindings["<FocusIn>"](None)
        self.assertEqual(owner.after.call_args.args[0], TOOLTIP_DELAY_MS)
        self.assertEqual(TOOLTIP_DELAY_MS, 275)
        widget.bindings["<FocusOut>"](None)
        owner.after_cancel.assert_called_once_with("pending")

    def test_icon_padding_preserves_minimum_hit_area(self):
        self.assertEqual(icon_button_padding(20), (4, 4))
        self.assertEqual(icon_button_padding(16), (6, 6))
        for size in (16, 20):
            horizontal, vertical = icon_button_padding(size)
            self.assertGreaterEqual(size + horizontal * 2, 28)
            self.assertGreaterEqual(size + vertical * 2, 28)

    def test_window_size_is_clamped_on_small_screens(self):
        self.assertEqual(constrained_size(1200, 900, 1024, 768), (984, 708))
        self.assertEqual(constrained_size(760, 500, 1440, 900), (760, 500))
        self.assertEqual(constrained_size(0, -2, 20, 20, horizontal_margin=30,
                                          vertical_margin=30), (1, 1))


class RoundedButtonHeadlessTest(unittest.TestCase):
    def button(self):
        button = RoundedButton.__new__(RoundedButton)
        button._state = "normal"
        button._pressed = button._focused = button._key_pressed = False
        button._hovered = False
        button._command = Mock()
        button._redraw = Mock()
        return button

    def test_focus_and_keyboard_press_release_are_visible_and_single_action(self):
        button = self.button()
        button._on_focus_in()
        self.assertTrue(button._focused)
        button._on_key_press()
        button._on_key_press()
        self.assertTrue(button._pressed)
        button._command.assert_not_called()
        button._on_key_release()
        button._command.assert_called_once_with()
        self.assertFalse(button._pressed)
        button._on_focus_out()
        self.assertFalse(button._focused)

    def test_mouse_release_during_space_press_does_not_double_invoke(self):
        button = self.button()
        button.winfo_width = lambda: 80
        button.winfo_height = lambda: 32
        button._on_key_press()
        button._on_release(SimpleNamespace(x=10, y=10))
        button._command.assert_not_called()
        button._on_key_release()
        button._command.assert_called_once_with()

    def test_disabled_keyboard_action_does_not_invoke(self):
        button = self.button()
        button._state = "disabled"
        self.assertEqual(button._on_key_press(), "break")
        self.assertEqual(button._on_key_release(), "break")
        button._command.assert_not_called()

    def test_focus_ring_is_two_pixels_and_separate_from_button_shape(self):
        button = self.button()
        button._focused = button._pressed = True
        button._variant = RoundedButton._VARIANTS["primary"]
        button._radius = 4
        button._padx = button._pady = 8
        button._text = "保存"
        button._font = button._image = None
        button.winfo_exists = lambda: True
        button.winfo_width = lambda: 80
        button.winfo_height = lambda: 32
        button.cget = lambda _key: theme.CANVAS
        button.delete = Mock()
        button.create_polygon = Mock(side_effect=(1, 2))
        button.create_text = Mock(return_value=3)
        RoundedButton._redraw(button)
        ring = button.create_polygon.call_args_list[0].kwargs
        self.assertEqual(ring["tags"], "focus-ring")
        self.assertEqual(ring["outline"], theme.FOCUS_RING)
        self.assertEqual(ring["width"], 2)

    def test_font_change_remeasures_actual_text_metrics(self):
        button = self.button()
        button._text = "保存"
        button._font = None
        button._image = None
        button._padx, button._pady = 12, 7
        button._auto_size = True
        button._shape_id = None
        font = FakeFont(24)
        with patch.object(tk.Canvas, "configure") as canvas_configure:
            button.configure(text="保存并继续", font=font)
        self.assertIs(button._font, font)
        self.assertEqual(button._text, "保存并继续")
        canvas_configure.assert_any_call(width=5 * 8 + 24, height=24 + 14)


class CalculatorLayoutHeadlessTest(unittest.TestCase):
    def test_empty_composition_is_unpacked_and_nonempty_has_one_compact_slot(self):
        tools = TableTools.__new__(TableTools)
        tools.composition = Mock()
        tools.composition_label = Mock()
        tools.selection_total_entry = Mock()
        tools.composition_label.winfo_manager.return_value = ""
        tools._render_composition("")
        tools.composition_label.pack_forget.assert_called_once_with()
        tools.composition_label.pack.assert_not_called()
        tools._render_composition("借方 1.00（1 项）")
        tools.composition_label.pack.assert_called_once_with(
            anchor="w", pady=(0, 5), before=tools.selection_total_entry)
        tools.composition_label.winfo_manager.return_value = "pack"
        tools._render_composition("借方 2.00（1 项）")
        tools.composition_label.pack.assert_called_once()


class TableFeedbackHeadlessTest(unittest.TestCase):
    def test_temporary_feedback_restores_latest_selection_summary(self):
        tools = TableTools.__new__(TableTools)
        tools.view = Mock()
        tools.stats = Mock()
        tools.stats_label = Mock()
        tools.summary = SelectionSummary()
        tools._feedback_job = None
        tools._feedback_active = False
        tools._selection_stats = "旧选区"
        tools.show_feedback("已保存", duration=None)
        tools.stats.set.assert_called_with("已保存")
        tools._set_selection_stats("新选区合计")
        tools.stats.set.assert_called_with("已保存")
        tools.clear_feedback()
        tools.stats.set.assert_called_with("新选区合计")
        self.assertFalse(tools._feedback_active)


if __name__ == "__main__":
    unittest.main()
