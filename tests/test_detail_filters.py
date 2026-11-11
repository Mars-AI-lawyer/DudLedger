import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.detail_filter_ui import DetailFilterDialog
from ledger_viewer.detail_filters import make_condition
from ledger_viewer.importer import classify_sheet
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import Application


def populate(store, directory):
    path = directory / "明细.csv"
    path.write_text(
        "日期,凭证号,科目编码,科目名称,摘要,原币金额,借方金额,贷方金额\n"
        "2025-01-01,1,1002,银行存款,借款 100%_A,1.000000000000000001,100.01,0\n"
        "2025-02-01,2,1002,银行存款,借款 100xxa,1.000000000000000002,100.02,0\n"
        "2025-03-01,3,1002,银行存款,借款 100%_a,无法识别,101,0\n"
        "2025-03-31,4,1002,银行存款,还款,-1.125,0,100\n"
        "2025-04-01,5,1002,银行存款,借款,,0,-100.01\n"
        "未知,6,1002,银行存款,借款,,500,0\n",
        encoding="utf-8",
    )
    store.import_sheet(classify_sheet(path, "CSV"))


class DetailFilterQueryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.store = LedgerStore(self.path / "test.sqlite")
        self.addCleanup(self.store.close)
        populate(self.store, self.path)

    def vouchers(self, *conditions):
        return [row["voucher_no"] for row in self.store.entries(conditions=conditions)]

    def test_dates_amount_boundaries_and_literal_text(self):
        condition = make_condition
        self.assertEqual(self.store.entry_months(), ("2025-01", "2025-02", "2025-03", "2025-04"))
        self.assertEqual(self.vouchers(condition("date", "gt", "2025-02-01")), ["3", "4", "5"])
        self.assertEqual(self.vouchers(condition("date", "between", "2025/02/01", "2025-03-01")), ["2", "3"])
        self.assertEqual(self.vouchers(condition("summary", "contains", "%_A")), ["1", "3"])
        self.assertEqual(self.vouchers(condition("credit", "le", "100")), ["4", "5"])
        self.assertEqual(self.vouchers(condition("credit", "lt", "-100")), ["5"])
        self.assertEqual(self.vouchers(condition("debit", "gt", "100.015")), ["2", "3", "6"])
        self.assertEqual(self.vouchers(condition("debit", "eq", "100.015")), [])
        self.assertEqual(self.vouchers(condition("original", "gt", "1.000000000000000001")), ["2"])
        for invalid in ("2025-02-29", "2025-13-01", ""):
            with self.assertRaises(ValueError):
                condition("date", "eq", invalid)

    def test_combination_reaches_later_pages_and_export_matches_query_order(self):
        source_id = self.store.sources()[0]["id"]
        self.store.db.executemany(
            """INSERT INTO entries(source_id,row_no,entry_date,fiscal_year,period,
                 voucher_no,voucher_type,account_key,summary,counterparty,auxiliary,
                 debit_cents,credit_cents,raw_json)
                 VALUES (?,?,'2025-03-02','2025','3',?,'','C:1002','借款 100%_A','','',?,0,'[]')""",
            ((source_id, index, str(index), 10100 + index) for index in range(10, 530)),
        )
        self.store.db.commit()
        filters = dict(
            account="C:1002", search="借款", period_to=("2025", "3"), sort_by="debit", descending=True,
            conditions=(make_condition("date", "months", "2025-01", "2025-03"),
                        make_condition("summary", "contains", "%_A"), make_condition("debit", "ge", "100.01")),
        )
        first, total, _ = self.store.entries_page(**filters)
        second, same_total, offset = self.store.entries_page(**filters, offset=500)
        self.assertEqual((len(first), len(second), total, same_total, offset), (500, 22, 522, 522, 500))
        output = self.path / "筛选明细.csv"
        reader = LedgerStore.connect_existing(self.store.path)
        try:
            self.assertEqual(reader.export_entries(output, **filters), total)
        finally:
            reader.close()
        with output.open(encoding="utf-8-sig") as handle:
            self.assertEqual([row["凭证号"] for row in csv.DictReader(handle)],
                             [row["voucher_no"] for row in first + second])


class DetailFilterUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        with patch("ledger_viewer.ui.Path.home", return_value=self.path):
            self.root = Application()
        self.app = next(iter(self.root.windows))
        self.addCleanup(self.app._close)
        self.app._use_book(self.path / "test.sqlite")
        populate(self.app.store, self.path)
        self.app.refresh_all()
        self.app.tabs.select(1)
        self.root.update()
        self.view = self.app.detail_view

    def dialog(self):
        return next(child for child in self.app.winfo_children() if isinstance(child, DetailFilterDialog))

    def click(self, x, y=10):
        self.view.event_generate("<ButtonPress-1>", x=x, y=y)
        self.view.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.root.update()

    def test_header_arrow_opens_filter_after_scrolling_and_title_still_sorts(self):
        self.view.configure(displaycolumns=("summary", "debit", "date", "credit", "counterparty"))
        self.view.column("summary", width=900)
        self.view.xview_moveto(1)
        self.root.update()
        x, _, width, _ = self.view.bbox(self.view.get_children()[0], "debit")
        self.click(x + width - 16)
        dialog = self.dialog()
        self.assertEqual(dialog.column, "debit")
        self.assertEqual(self.app.detail_sort, "date")
        dialog.operator.set("小于或等于 ≤")
        dialog._operator_changed()
        dialog.start.set("100.01")
        dialog.apply.invoke()
        self.root.update()
        self.assertEqual(self.app.detail_total, 1)
        self.assertIn("已筛选 1 项", self.app.detail_info.get())
        self.assertEqual(self.view.heading("debit", "image"), (str(self.app.detail_filter_images[1]),))
        self.click(x + 20)
        self.assertEqual(self.app.detail_sort, "debit")
        self.assertEqual(self.app.detail_total, 1)
        self.assertFalse(self.app.detail_next.winfo_ismapped())
        self.view.selection_set(self.view.get_children()[0])
        self.root.update()
        self.assertTrue(self.app.detail_mark.instate(("!disabled",)))
        with patch.object(self.app, "open_voucher") as open_voucher:
            self.app.detail_mark.invoke()
            open_voucher.assert_not_called()
            self.assertTrue(self.app.store.db.execute("SELECT 1 FROM voucher_marks").fetchone())

    def test_more_edits_hidden_conditions_and_search_applies_on_return(self):
        self.view.configure(displaycolumns=("debit", "credit"))
        self.app._open_detail_filter("date")
        dialog = self.dialog()
        dialog.operator.set("按自然月多选")
        dialog._operator_changed()
        dialog.month_list.selection_set(0)
        dialog.month_list.selection_set(2)
        dialog.apply.invoke()
        self.assertEqual(self.app.detail_total, 3)
        self.app.search.set("借款")
        self.app.sort_details("date")
        self.assertEqual(self.app.detail_total, 3)
        self.app.detail_entry.focus_force()
        self.root.update()
        self.app.detail_entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.app.detail_total, 2)
        menu = self.app.nametowidget(self.app.detail_more.cget("menu"))
        menu.invoke(1)
        dialog = self.dialog()
        self.assertEqual(dialog.column, "date")
        self.assertEqual(dialog.month_list.curselection(), (0, 2))
        dialog.clear.invoke()
        self.assertEqual(self.app.detail_total, 5)
        self.app.clear_filter()
        self.assertEqual(self.app.detail_total, 6)


if __name__ == "__main__":
    unittest.main()
