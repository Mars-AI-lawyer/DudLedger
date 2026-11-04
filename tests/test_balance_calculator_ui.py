import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.importer import classify_sheet
from ledger_viewer.ui import Application


class BalanceCalculatorUiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch("ledger_viewer.ui.Path.home", return_value=self.base):
            self.root = Application()
        self.app = next(iter(self.root.windows))
        path = self.base / "科目余额.csv"
        path.write_text("科目编码,科目名称,期初借方,期初贷方,本期借方,本期贷方,本年累计借方,本年累计贷方,期末借方,期末贷方\n"
                        "1002,银行存款,1000,0,120,20,600,100,1100,0\n"
                        "100201,基本户,600,0,70,10,350,50,660,0\n"
                        "100202,一般户,400,0,50,10,250,50,440,0\n"
                        "2241,其他应付款,0,200,0,40,0,100,0,240\n", encoding="utf-8")
        self.app._use_book(self.base / "book.sqlite")
        self.app.store.import_sheet(classify_sheet(path, "CSV"))
        self.app.refresh_all()
        self.app.tabs.select(0)
        self.view = self.app.summary_view
        self.tools = self.app.balance_tools
        self.app._change_columns("balance", ["code", "name", "pd", "pc", "cd", "cc"])
        self.root.update()

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def test_collapsed_children_do_not_enter_dragged_balance_range(self):
        start = self.view.bbox("C:1002", "pd")
        end = self.view.bbox("C:2241", "pc")
        self.view.event_generate("<Button-1>", x=start[0] + 10, y=start[1] + 10)
        self.view.event_generate("<B1-Motion>", x=end[0] + 10, y=end[1] + 10)
        self.view.event_generate("<ButtonRelease-1>", x=end[0] + 10, y=end[1] + 10)
        self.root.update()
        self.assertEqual(self.tools.selection_range()[0], ("C:1002", "C:2241"))
        self.assertEqual(self.tools.summary.numbers, 3)
        self.assertEqual(self.tools.summary.total, Decimal("180.00"))
        self.assertEqual(self.tools.selection_total.get(), "180.00")
        self.tools.calculate(1)
        snapshot = self.tools.calculation.items[0].snapshot
        self.assertEqual(len(snapshot.cells), 3)
        self.assertIn("科目余额.csv", snapshot.cells[0].source)
        self.assertIn("银行存款", snapshot.cells[0].source)
        self.assertFalse(self.tools.panel.winfo_ismapped())

    def test_expanded_child_rows_can_be_summed_and_copied_together(self):
        self.app.set_expansion(True)
        self.root.update()
        self.tools.select("C:100201", "pd")
        self.tools.select("C:100202", "pd", extend=True)
        self.assertEqual(self.tools.selection_range()[0], ("C:100201", "C:100202"))
        self.assertEqual(self.tools.summary.total, Decimal("120.00"))
        with patch.object(self.view, "clipboard_clear"), patch.object(self.view, "clipboard_append") as copied:
            self.tools.copy_cell()
            copied.assert_called_once_with("70.00\n50.00")
        self.tools.calculate(1)
        self.tools.select("C:100201", "pc")
        self.tools.calculate(-1)
        self.assertEqual(self.tools.result.get(), "110.00")
        self.tools.undo()
        self.assertEqual(self.tools.result.get(), "120.00")

    def test_arrow_extension_follows_visible_hierarchy_order(self):
        self.app.set_expansion(True)
        self.tools.select("C:1002", "pd")
        self.tools._move("Down", extend=True)
        self.assertEqual(self.tools.active, ("C:100201", "pd"))
        self.assertEqual(self.tools.summary.total, Decimal("190.00"))
        self.tools._move("Down", extend=True)
        self.assertEqual(self.tools.active, ("C:100202", "pd"))
        self.tools._move("Down", extend=True)
        self.assertEqual(self.tools.active, ("C:2241", "pd"))

    def test_collapsing_selected_children_clears_preview_and_keeps_calculation(self):
        self.app.set_expansion(True)
        self.tools.select("C:100201", "pd")
        self.tools.select("C:100202", "pd", extend=True)
        self.tools.calculate(1)
        self.app.set_expansion(False)
        self.root.update()
        self.assertIsNone(self.tools.active)
        self.assertEqual(self.tools.selection_total.get(), "—")
        self.assertEqual(self.tools.result.get(), "120.00")

    def test_all_balance_amount_columns_count_but_account_codes_and_row_counts_do_not(self):
        self.app._change_columns("balance", ["code", "n", "od", "oc", "pd", "pc", "yd", "yc", "cd", "cc"])
        self.tools.select("C:1002", "code")
        self.tools.select("C:1002", "cc", extend=True)
        self.assertEqual(self.tools.summary.numbers, 6)
        self.assertEqual(self.tools.summary.total, Decimal("2940.00"))

    def test_balance_tree_expand_and_double_click_drilldown_are_preserved(self):
        box = self.view.bbox("C:1002", "#0")
        self.view.event_generate("<Button-1>", x=box[0] + 30, y=box[1] + 10)
        self.view.event_generate("<ButtonRelease-1>", x=box[0] + 30, y=box[1] + 10)
        self.root.update()
        self.assertTrue(self.view.bbox("C:100201"))
        self.tools.select("C:100201", "pd")
        self.view.selection_set("C:100201")
        self.app.open_account(None)
        self.assertEqual(self.app.tabs.index("current"), 1)
        self.assertEqual(self.app.account, "C:100201")

    def test_balance_filter_preserves_committed_sources_and_close_clears_both_calculators(self):
        self.tools.select("C:1002", "pd")
        self.tools.calculate(1)
        snapshot = self.tools.calculation.items[0].snapshot
        self.app.summary_search.set("其他应付款")
        self.app.refresh_all()
        self.root.update()
        self.assertIsNone(self.tools.active)
        self.assertEqual(self.tools.result.get(), "120.00")
        self.assertIs(self.tools.calculation.items[0].snapshot, snapshot)
        self.app.close_book()
        self.assertEqual(self.tools.result.get(), "0.00")
        self.assertEqual(self.app.detail_tools.result.get(), "0.00")


if __name__ == "__main__":
    unittest.main()
