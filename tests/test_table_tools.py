import tempfile
import unittest
import csv
import io
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tkinter import ttk

from ledger_viewer.importer import classify_sheet
from ledger_viewer.table_tools import Calculation, cell_number
from ledger_viewer.ui import Application


class CalculationTest(unittest.TestCase):
    def test_exact_add_subtract_negatives_and_undo(self):
        calc = Calculation()
        calc.add(1, "0.10", "a")
        calc.add(1, "0.20", "b")
        calc.add(-1, "(1,000.30)", "c")
        self.assertEqual(calc.total, Decimal("1000.60"))
        calc.undo()
        self.assertEqual(calc.total, Decimal("0.30"))
        calc.clear()
        self.assertEqual(calc.total, 0)

    def test_blank_text_and_nonfinite_values_do_not_become_zero(self):
        for value in ("", "—", "银行存款", "NaN", "Infinity"):
            with self.assertRaises(ValueError):
                cell_number(value)

    def test_original_currency_precision_is_preserved(self):
        calc = Calculation()
        calc.add(1, "123456789012345678901234567890.12345", "a")
        calc.add(-1, "0.00001", "b")
        self.assertEqual(calc.total, Decimal("123456789012345678901234567890.12344"))


class TableToolsUiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch("ledger_viewer.ui.Path.home", return_value=self.base):
            self.root = Application()
        self.window = next(iter(self.root.windows))
        path = self.base / "账.csv"
        path.write_text("日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                        "2026-01-01,1,1002,银行存款,完整摘要不可截断,0.1,0\n"
                        "2026-01-01,1,6001,主营业务收入,另一条摘要,0,0.2\n", encoding="utf-8")
        self.window._use_book(self.base / "book.sqlite")
        self.window.store.import_sheet(classify_sheet(path, "CSV"))
        self.window.refresh_all()
        self.window.tabs.select(1)
        self.root.update()
        self.view = self.window.detail_view
        self.tools = self.window.detail_tools

    def tearDown(self):
        self.window._close()
        self.temp.cleanup()

    def test_ctrl_click_adds_disjoint_cells_without_filling_holes(self):
        self.view.configure(displaycolumns=("debit", "credit"))
        self.root.update()
        first, second = self.view.get_children()
        self.view.set(first, "credit", "99.00")
        self.view.set(second, "debit", "88.00")
        for row, column, state in ((first, "debit", 0), (second, "credit", 4)):
            x, y, w, h = self.view.bbox(row, column)
            self.view.event_generate("<ButtonPress-1>", x=x + w // 2, y=y + h // 2, state=state)
            self.view.event_generate("<ButtonRelease-1>", x=x + w // 2, y=y + h // 2, state=state)
        self.root.update()
        self.assertEqual(self.tools.summary.total, Decimal("0.30"))
        self.assertEqual(self.tools.summary.cells, 2)
        self.assertEqual(len(self.tools.selection_ranges()), 2)
        self.assertTrue(all(border.winfo_ismapped() for border in self.tools.borders))
        self.tools.calculate(1)
        self.assertEqual(self.tools.result.get(), "0.30")
        self.assertEqual([(cell.row, cell.column) for cell in self.tools.calculation.items[0].snapshot.cells],
                         [(first, "debit"), (second, "credit")])
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copy:
            self.tools.copy_cell()
        self.assertEqual(copy.call_args.args[0], "0.10\t\n\t0.20")
        self.tools.select(first, "debit")
        self.assertEqual(len(self.tools.selection_ranges()), 1)

    def test_overlapping_rectangles_deduplicate_and_context_respects_actual_cells(self):
        first, second = self.view.get_children()
        self.tools.select(first, "debit")
        self.tools.select(second, "credit", extend=True)
        self.tools.select(second, "credit", additive=True)
        self.assertEqual(self.tools.summary.cells, 4)
        self.assertEqual(self.tools.summary.numbers, 2)
        self.assertEqual(self.tools.summary.total, Decimal("0.30"))
        self.tools.calculate(1)
        self.assertEqual(len(self.tools.calculation.items[0].snapshot.cells), 2)
        self.tools.undo()
        self.assertEqual(self.tools.result.get(), "0.00")
        self.view.configure(displaycolumns=("debit", "credit"))
        self.root.update()
        self.tools.select(first, "debit")
        self.tools.select(second, "credit", additive=True)
        x, y, w, h = self.view.bbox(first, "debit")
        with patch.object(self.tools.menu, "tk_popup"), patch.object(self.tools.menu, "grab_release"):
            self.tools._context(SimpleNamespace(x=x + 10, y=y + 10, x_root=0, y_root=0))
        self.assertEqual(self.tools.summary.cells, 2)
        x, y, w, h = self.view.bbox(first, "credit")
        with patch.object(self.tools.menu, "tk_popup"), patch.object(self.tools.menu, "grab_release"):
            self.tools._context(SimpleNamespace(x=x + 10, y=y + 10, x_root=0, y_root=0))
        self.assertEqual(self.tools.selected_cells(), {(first, "credit")})

    def test_ctrl_drag_adds_rectangle_and_sort_clears_all_ranges(self):
        self.view.configure(displaycolumns=("summary", "debit", "credit"))
        self.root.update()
        first, second = self.view.get_children()
        self.tools.select(first, "summary")
        start, end = self.view.bbox(first, "debit"), self.view.bbox(second, "credit")
        self.view.event_generate("<ButtonPress-1>", x=start[0] + 10, y=start[1] + 10, state=4)
        self.view.event_generate("<B1-Motion>", x=end[0] + 10, y=end[1] + 10, state=260)
        self.view.event_generate("<ButtonRelease-1>", x=end[0] + 10, y=end[1] + 10, state=4)
        self.assertEqual(len(self.tools.selection_ranges()), 2)
        self.assertEqual(self.tools.summary.cells, 5)
        self.assertEqual(self.tools.summary.total, Decimal("0.30"))
        self.window.sort_details("credit")
        self.assertFalse(self.tools.selection_ranges())
        self.assertFalse(self.tools.saved_ranges)

    def test_selected_cell_copies_full_text_and_blank_with_hidden_reordered_columns(self):
        self.view.configure(displaycolumns=("summary", "credit", "debit"))
        self.root.update()
        row = self.view.get_children()[0]
        box = self.view.bbox(row, "summary")
        self.tools._hit(SimpleNamespace(x=box[0] + 10, y=box[1] + 10))
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copy:
            self.view.event_generate("<<Copy>>")
            self.root.update()
            copy.assert_called_with("完整摘要不可截断")
            self.tools.select(row, "credit")
            self.tools.copy_cell()
            copy.assert_called_with("")
        self.assertEqual(str(self.tools.plus["state"]), "disabled")

    def test_sidebar_add_subtract_does_not_change_table_and_keeps_history(self):
        first, second = self.view.get_children()
        original = self.view.item(first, "values")
        self.tools.toggle()
        self.root.update()
        self.assertTrue(self.tools.panel.winfo_ismapped())
        self.tools.select(first, "debit")
        self.tools.plus.invoke()
        self.tools.select(second, "credit")
        self.tools.minus.invoke()
        self.assertEqual(self.tools.result.get(), "-0.10")
        self.assertEqual(len(self.tools.calculation.items), 2)
        self.assertEqual(self.view.item(first, "values"), original)
        self.tools.toggle()
        self.tools.toggle()
        self.assertEqual(self.tools.result.get(), "-0.10")
        self.tools.undo()
        self.assertEqual(self.tools.result.get(), "0.10")
        with patch.object(self.tools.result_entry, "event_generate") as copy:
            self.tools._copy_result_selection(None)
            copy.assert_called_once_with("<<Copy>>")
        self.tools.clear()
        self.assertEqual(self.tools.result.get(), "0.00")

    def test_keyboard_copy_uses_the_selected_cell_without_a_copy_button(self):
        row = self.view.get_children()[0]
        self.tools.select(row, "summary")
        self.view.focus_force()
        self.root.update()
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copy:
            for shortcut in ("<Control-c>", "<Command-c>"):
                copy.reset_mock()
                self.view.event_generate(shortcut)
                self.root.update()
                copy.assert_called_once_with("完整摘要不可截断")
        pending = [self.window]
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, ttk.Button):
                self.assertNotIn(widget.cget("text"), ("复制单元格", "复制结果"))

    def test_hidden_selected_column_cannot_be_copied_or_used_for_calculation(self):
        self.tools.select(self.view.get_children()[0], "debit")
        self.window._change_columns("detail", ["date", "summary", "credit"])
        self.root.update()
        self.assertIsNone(self.tools.active)
        self.tools.calculate(1)
        self.assertEqual(self.tools.result.get(), "0.00")

    def test_refresh_clears_cell_so_copy_cannot_pick_up_a_different_row(self):
        self.tools.select(self.view.get_children()[0], "debit")
        self.window.sort_details("credit")
        self.assertIsNone(self.tools.active)
        with patch.object(self.view, "clipboard_clear") as clear:
            self.tools.copy_cell()
            clear.assert_not_called()

    def test_voucher_has_independent_calculator_and_cell_copy(self):
        row = self.view.get_children()[0]
        self.view.selection_set(row)
        self.window.open_voucher(None)
        self.root.update()
        dialog = next(iter(self.window.voucher_windows))
        tools = dialog.cell_tools
        self.assertIsNot(tools, self.tools)
        tools.toggle()
        tools.select(tools.view.get_children()[0], "debit")
        tools.calculate(1)
        self.assertEqual(tools.result.get(), "0.10")
        tools.select(tools.view.get_children()[0], "summary")
        with patch.object(tools.view, "clipboard_clear"), patch.object(tools.view, "clipboard_append") as copy:
            tools.copy_cell()
            copy.assert_called_with("完整摘要不可截断")
        dialog.destroy()

    def test_drag_rectangle_updates_sum_and_count_with_sidebar_closed(self):
        self.view.configure(displaycolumns=("summary", "debit", "credit"))
        self.root.update()
        first, second = self.view.get_children()
        start = self.view.bbox(first, "debit")
        end = self.view.bbox(second, "credit")
        self.view.event_generate("<Button-1>", x=start[0] + 12, y=start[1] + 12)
        self.view.event_generate("<B1-Motion>", x=end[0] + 12, y=end[1] + 12)
        self.root.update()
        self.view.event_generate("<ButtonRelease-1>", x=end[0] + 12, y=end[1] + 12)
        self.root.update()
        self.assertEqual(self.tools.selection_range(), ((first, second), ("debit", "credit")))
        self.assertEqual(self.tools.summary.cells, 4)
        self.assertEqual(self.tools.summary.count, 2)
        self.assertEqual(self.tools.summary.numbers, 2)
        self.assertEqual(self.tools.summary.total, Decimal("0.30"))
        self.assertIn("合计 0.30", self.tools.stats.get())
        self.assertFalse(self.tools.panel.winfo_ismapped())
        self.assertTrue(all(border.winfo_ismapped() for border in self.tools.borders))
        self.assertIsNone(self.tools.scroll_job)

    def test_reverse_range_in_display_order_copies_rectangle_and_sums_only_money(self):
        self.view.configure(displaycolumns=("credit", "code", "debit"))
        first, second = self.view.get_children()
        self.tools.select(second, "debit")
        self.tools.select(first, "credit", extend=True)
        self.assertEqual(self.tools.summary.count, 4)
        self.assertEqual(self.tools.summary.numbers, 2)
        self.assertEqual(self.tools.summary.total, Decimal("0.30"))
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copy:
            self.tools.copy_cell()
        self.assertEqual(copy.call_args.args[0], "\t1002\t0.10\n0.20\t6001\t")

    def test_range_arithmetic_is_one_undoable_operation_and_shift_extends(self):
        first, second = self.view.get_children()
        self.tools.select(first, "debit")
        self.tools._move("Down", extend=True)
        self.assertEqual(self.tools.selection_range()[0], (first, second))
        self.tools._move("Right", extend=True)
        self.tools.calculate(1)
        self.assertEqual(self.tools.result.get(), "0.30")
        self.assertEqual(len(self.tools.calculation.items), 1)
        self.tools.select(second, "credit")
        self.tools.calculate(-1)
        self.assertEqual(self.tools.result.get(), "0.10")
        self.tools.undo()
        self.assertEqual(self.tools.result.get(), "0.30")
        self.tools.undo()
        self.assertEqual(self.tools.result.get(), "0.00")

    def test_multiline_text_in_range_keeps_rows_when_pasted_into_spreadsheet(self):
        self.view.configure(displaycolumns=("summary", "debit"))
        first, second = self.view.get_children()
        self.view.set(first, "summary", "有制表符\t和换行\n的摘要")
        self.tools.select(first, "summary")
        self.tools.select(second, "debit", extend=True)
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copy:
            self.tools.copy_cell()
        self.assertEqual(list(csv.reader(io.StringIO(copy.call_args.args[0]), delimiter="\t")),
                         [["有制表符\t和换行\n的摘要", "0.10"], ["另一条摘要", ""]])

    def test_range_invalidates_on_sort_and_heading_mouse_release_still_sorts(self):
        self.view.configure(displaycolumns=("credit", "debit"))
        self.root.update()
        first, second = self.view.get_children()
        self.tools.select(first, "debit")
        self.tools.select(second, "debit", extend=True)
        self.view.event_generate("<Button-1>", x=self.view.column("#0", "width")+30, y=8)
        self.view.event_generate("<ButtonRelease-1>", x=self.view.column("#0", "width")+30, y=8)
        self.root.update()
        self.assertEqual(self.window.detail_sort, "credit")
        self.assertIsNone(self.tools.anchor)
        self.assertEqual(self.tools.summary.cells, 0)

    def test_autoscroll_extends_selection_and_release_cancels_timer(self):
        for index in range(120):
            self.view.insert("", "end", iid=f"extra{index}", values=("",) * 13 + ("1.00", "", "", ""))
        self.view.configure(displaycolumns=("debit", "credit"))
        self.root.update()
        first = self.view.get_children()[0]
        start = self.view.bbox(first, "debit")
        self.tools._click(SimpleNamespace(x=start[0] + 10, y=start[1] + 10, state=0))
        self.tools.drag_point = (start[0] + 10, self.view.winfo_height() + 20)
        self.tools._autoscroll()
        self.root.update()
        self.assertGreater(self.view.yview()[0], 0)
        self.assertGreater(len(self.tools.selection_range()[0]), 2)
        self.tools._release(SimpleNamespace(x=start[0] + 10, y=self.view.winfo_height() + 20))
        self.assertIsNone(self.tools.scroll_job)
        self.assertFalse(self.tools.dragging)

    def test_live_selection_preview_does_not_commit_or_reopen_sidebar(self):
        first, second = self.view.get_children()
        self.tools.select(first, "debit")
        self.assertEqual(self.tools.selection_total.get(), "0.10")
        self.assertEqual(self.tools.result.get(), "0.00")
        self.assertFalse(self.tools.panel.winfo_ismapped())
        self.tools.toggle()
        self.tools.plus.invoke()
        self.tools.select(second, "credit")
        self.assertEqual(self.tools.selection_total.get(), "0.20")
        self.assertEqual(self.tools.result.get(), "0.10")
        self.assertEqual(len(self.tools.history.get_children()), 1)
        self.tools.toggle()
        self.tools.select(first, "debit")
        self.root.update()
        self.assertFalse(self.tools.panel.winfo_ismapped())
        self.tools.toggle()
        self.assertEqual(self.tools.selection_total.get(), "0.10")
        self.assertEqual(self.tools.result.get(), "0.10")

    def test_committed_snapshot_survives_changed_cells_sort_and_filter(self):
        first, second = self.view.get_children()
        original_source = self.view.set(first, "source")
        self.tools.select(first, "debit")
        self.tools.select(second, "credit", extend=True)
        self.tools.calculate(1)
        step = self.tools.calculation.items[0]
        self.assertEqual([cell.text for cell in step.snapshot.cells], ["0.10", "0.20"])
        self.assertEqual(step.snapshot.cells[0].source, original_source)
        self.view.set(first, "debit", "9000.00")
        self.view.set(first, "source", "新的来源")
        self.window.sort_details("credit")
        self.window.search.set("不存在的查询")
        self.window.submit_detail_search()
        self.root.update()
        self.assertEqual(self.tools.selection_total.get(), "—")
        self.assertEqual(self.tools.result.get(), "0.30")
        self.assertIs(self.tools.calculation.items[0], step)
        self.tools.history.selection_set("0")
        self.tools._show_history_detail()
        detail = self.tools.history_detail.get("1.0", "end-1c")
        self.assertIn(original_source, detail)
        self.assertIn("借方：0.10", detail)
        self.assertIn("贷方：0.20", detail)
        self.assertNotIn("9000.00", detail)
        self.assertNotIn("新的来源", detail)

    def test_committing_reads_fresh_values_then_freezes_them(self):
        first = self.view.get_children()[0]
        self.tools.select(first, "debit")
        self.view.set(first, "debit", "0.40")
        self.tools.calculate(1)
        self.assertEqual(self.tools.result.get(), "0.40")
        self.assertEqual(self.tools.calculation.items[0].snapshot.cells[0].text, "0.40")

    def test_named_steps_undo_whole_range_and_clear_keeps_current_preview(self):
        first, second = self.view.get_children()
        self.tools.select(first, "debit")
        self.tools.calculate(1)
        self.tools.select(second, "credit")
        self.tools.calculate(1)
        self.tools.select(first, "debit")
        self.tools.calculate(-1)
        self.assertEqual([step.label for step in self.tools.calculation.items], ["选区 A", "选区 B", "选区 C"])
        self.assertEqual(self.tools.history.item("2", "values"), ("－ 选区 C（1 项）", "0.10"))
        self.assertEqual(self.tools.result.get(), "0.20")
        self.tools.undo_button.invoke()
        self.assertEqual(self.tools.result.get(), "0.30")
        self.tools.clear_button.invoke()
        self.assertEqual(self.tools.result.get(), "0.00")
        self.assertEqual(self.tools.selection_total.get(), "0.10")
        self.assertEqual(self.tools.history.get_children(), ())
        self.assertEqual(str(self.tools.undo_button["state"]), "disabled")
        self.tools.calculate(1)
        self.assertEqual(self.tools.calculation.items[0].label, "选区 A")

    def test_voucher_drag_release_finishes_range_and_calculator_fits_window(self):
        self.window.open_voucher(None, entry_id=int(self.view.get_children()[0]))
        dialog = next(iter(self.window.voucher_windows))
        self.root.update()
        tools = dialog.cell_tools
        compact_height = dialog.winfo_height()
        tools.toggle()
        self.root.update()
        tools.view.configure(displaycolumns=("debit", "credit", "source"))
        self.root.update()
        first, second = tools.view.get_children()
        start = tools.view.bbox(first, "debit")
        end = tools.view.bbox(second, "credit")
        tools.view.event_generate("<Button-1>", x=start[0] + 10, y=start[1] + 10)
        tools.view.event_generate("<B1-Motion>", x=end[0] + 10, y=end[1] + 10)
        tools.view.event_generate("<ButtonRelease-1>", x=end[0] + 10, y=end[1] + 10)
        self.root.update()
        self.assertFalse(tools.dragging)
        self.assertIsNone(tools.scroll_job)
        self.assertEqual(tools.selection_total.get(), "0.30")
        tools.plus.invoke()
        self.assertEqual(tools.result.get(), "0.30")
        self.assertEqual(self.tools.result.get(), "0.00")
        self.assertLessEqual(tools.clear_button.winfo_rooty() + tools.clear_button.winfo_height(),
                             dialog.winfo_rooty() + dialog.winfo_height())
        tools.toggle()
        self.root.update()
        self.assertLessEqual(dialog.winfo_height(), compact_height + 2)
        dialog.destroy()

    def test_larger_font_keeps_calculator_actions_and_result_inside_window(self):
        self.root.set_font_size(20, persist=False)
        self.window.geometry("820x520")
        self.root.update()
        self.tools.toggle()
        self.root.update()
        for widget in (self.tools.plus, self.tools.minus, self.tools.result_entry, self.tools.undo_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.window.winfo_rooty() + self.window.winfo_height())

    def test_native_column_separator_drag_resizes_after_cell_selection(self):
        self.view.configure(displaycolumns=("summary", "debit", "credit"))
        self.root.update()
        first = self.view.get_children()[0]
        self.tools.select(first, "debit")
        box = self.view.bbox(first, "summary")
        edge = box[0] + box[2] - 1
        self.assertEqual(self.view.identify_region(edge, 8), "separator")
        before = int(self.view.column("summary", "width"))
        self.view.event_generate("<Button-1>", x=edge, y=8)
        self.view.event_generate("<B1-Motion>", x=edge + 55, y=8)
        self.view.event_generate("<ButtonRelease-1>", x=edge + 55, y=8)
        self.root.update()
        self.assertGreater(int(self.view.column("summary", "width")), before + 35)
        self.assertEqual(self.tools.active, (first, "debit"))
        self.tools.calculate(1)
        self.assertEqual(self.tools.result.get(), "0.10")

    def test_old_and_updated_column_preferences_keep_focus_last(self):
        from ledger_viewer.ui import visible_columns
        saved = ["marked", "date", "summary", "debit"]
        self.assertEqual(visible_columns("detail", saved), ["date", "summary", "debit", "marked"])
        self.assertNotIn("marked", visible_columns("detail", ["date", "debit"]))
        self.window._change_columns("detail", saved)
        self.assertEqual(tuple(self.view.cget("displaycolumns")), ("date", "summary", "debit", "marked"))
        self.assertEqual(self.window.store.display_columns("detail"), ["date", "summary", "debit", "marked"])

    def test_scrolling_large_selected_range_does_not_recalculate_its_sum(self):
        for index in range(200):
            self.view.insert("", "end", iid=f"perf{index}", values=("",) * 13 + ("1.00", "", "", ""))
        first = self.view.get_children()[0]
        self.tools.select(first, "debit")
        self.tools.select("perf199", "debit", extend=True)
        self.root.update()
        total = self.tools.selection_total.get()
        with patch.object(self.tools, "_update_summary", wraps=self.tools._update_summary) as update:
            self.view.yview_scroll(10, "units")
            self.root.update()
            update.assert_not_called()
        self.assertEqual(self.tools.selection_total.get(), total)


if __name__ == "__main__":
    unittest.main()
