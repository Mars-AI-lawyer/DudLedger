import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer.detail_filter_ui import DetailFilterDialog
from ledger_viewer.detail_filters import make_condition
from ledger_viewer.importer import classify_sheet
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import App, Application


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

    def test_voucher_heading_sorts_by_number_across_voucher_types(self):
        with self.store.db:
            self.store.db.execute("UPDATE entries SET voucher_no='10', voucher_type='收' WHERE voucher_no='1'")
            self.store.db.execute("UPDATE entries SET voucher_type='付' WHERE voucher_no='2'")
        for descending, expected in ((False, ['2', '3', '4', '5', '6', '10']),
                                     (True, ['10', '6', '5', '4', '3', '2'])):
            self.assertEqual([row['voucher_no'] for row in self.store.entries(sort_by='voucher', descending=descending)], expected)

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

    def test_original_amount_sorts_exact_decimals_and_export_uses_same_order(self):
        filters = dict(sort_by="original", descending=True)
        reader = LedgerStore.connect_existing(self.store.path)
        try:
            rows = reader.entries(**filters)
            numeric = [row["voucher_no"] for row in rows if row["voucher_no"] in ("1", "2", "4")]
            self.assertEqual(numeric, ["2", "1", "4"])
            output = self.path / "原币排序.csv"
            reader.export_entries(output, **filters)
        finally:
            reader.close()
        with output.open(encoding="utf-8-sig") as handle:
            self.assertEqual([row["凭证号"] for row in csv.DictReader(handle)], [row["voucher_no"] for row in rows])


class DetailHeadingHeadlessTest(unittest.TestCase):
    def test_sort_new_column_descending_then_ascending(self):
        app = SimpleNamespace(detail_sort='date', detail_descending=False, refresh_details=Mock(),
                              detail_conditions={'summary': make_condition('summary', 'contains', '借款')})
        for column, descending in [('date', True), ('date', False), ('debit', True), ('debit', False), ('voucher', True)]:
            App.sort_details(app, column)
            self.assertEqual((app.detail_sort, app.detail_descending), (column, descending))
        self.assertEqual(app.refresh_details.call_count, 5)
        self.assertIn('summary', app.detail_conditions)

    def test_right_click_maps_visible_column_and_offers_filter_and_hide(self):
        view = Mock()
        view.identify_region.return_value = 'heading'
        view.identify_column.return_value = '#2'
        view.column.return_value = 'voucher'
        app = SimpleNamespace(detail_view=view, detail_heading_menu=Mock(), detail_restore_menu=Mock(),
            _shown_detail_columns=Mock(return_value=('credit', 'voucher')), _has_detail_filter=Mock(return_value=False),
            _open_detail_filter=Mock(), _set_detail_column_visibility=Mock())
        event = SimpleNamespace(x=45, y=10, x_root=100, y_root=75)
        self.assertEqual(App._detail_heading_context(app, event), 'break')
        view.column.assert_called_once_with('#2', 'id')
        actions = {call.kwargs['label']: call.kwargs for call in app.detail_heading_menu.add_command.call_args_list}
        actions['筛选此列…']['command']()
        actions['隐藏此列']['command']()
        app._open_detail_filter.assert_called_once_with('voucher')
        app._set_detail_column_visibility.assert_called_once_with('voucher', False)
        app.detail_heading_menu.tk_popup.assert_called_once_with(100, 75)
        view.identify_region.return_value = 'cell'
        self.assertIsNone(App._detail_heading_context(app, event))
        app._shown_detail_columns.return_value = ('voucher',)
        app._change_columns = Mock()
        App._set_detail_column_visibility(app, 'voucher', False)
        app._change_columns.assert_not_called()
        App._set_detail_column_visibility(app, 'debit', True)
        app._change_columns.assert_called_once_with('detail', ['voucher', 'debit'])

    def test_header_commands_sort_and_context_actions_have_separate_bindings(self):
        app = SimpleNamespace(detail_view=Mock(), tk=Mock(), sort_details=Mock(), _open_detail_filter=Mock(),
                              _detail_heading_context=Mock())
        app.tk.call.return_value = 'aqua'
        App._bind_detail_headings(app)
        commands = {call.args[0]: call.kwargs['command'] for call in app.detail_view.heading.call_args_list}
        for column in ('debit', 'credit', 'original', 'date', 'voucher', 'summary'):
            commands[column]()
            app.sort_details.assert_called_with(column)
        app._open_detail_filter.assert_not_called()
        self.assertEqual([call.args[0] for call in app.detail_view.bind.call_args_list],
                         ['<Button-2>', '<Button-3>', '<Control-Button-1>'])


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

    def test_header_sorts_and_context_menu_opens_filter_after_scrolling(self):
        self.view.configure(displaycolumns=("summary", "debit", "date", "credit", "counterparty"))
        self.view.column("summary", width=900)
        self.view.xview_moveto(1)
        self.root.update()
        x, _, width, _ = self.view.bbox(self.view.get_children()[0], "debit")
        self.click(x + width - 16)
        self.assertEqual((self.app.detail_sort, self.app.detail_descending), ('debit', True))
        self.assertFalse(any(isinstance(child, DetailFilterDialog) for child in self.app.winfo_children()))
        with patch.object(self.app.detail_heading_menu, 'tk_popup'):
            self.view.event_generate('<Button-3>', x=x + width - 16, y=10)
        self.app.detail_heading_menu.invoke(0)
        dialog = self.dialog()
        self.assertEqual(dialog.column, "debit")
        dialog.operator.set("小于或等于 ≤")
        dialog._operator_changed()
        dialog.start.set("100.01")
        dialog.apply.invoke()
        self.root.update()
        self.assertEqual(self.app.detail_total, 1)
        self.assertIn("已筛选 1 项", self.app.detail_info.get())
        self.assertEqual(self.view.heading("debit", "image"), (str(self.app.detail_filter_images[1]),))
        self.click(x + width - 16)
        self.assertEqual(self.app.detail_sort, "debit")
        self.assertFalse(self.app.detail_descending)
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
        dialog.month_selection.choose('2025-01', True)
        dialog.month_selection.choose('2025-03', True)
        dialog.apply.invoke()
        self.assertEqual(self.app.detail_total, 3)
        self.app._open_detail_filter('summary')
        summary = self.dialog()
        summary.start.set('借款')
        summary.apply.invoke()
        self.root.update()
        self.assertEqual(self.app.detail_total, 2)
        menu = self.app.nametowidget(self.app.detail_more.cget("menu"))
        menu.invoke(1)
        dialog = self.dialog()
        self.assertEqual(dialog.column, "date")
        self.assertEqual(dialog.month_selection.selected, {'2025-01', '2025-03'})
        dialog.clear.invoke()
        self.assertEqual(self.app.detail_total, 5)
        self.app.clear_filter()
        self.assertEqual(self.app.detail_total, 6)

    def test_sort_directions_do_not_clear_conditions(self):
        self.app._set_detail_filter("summary", make_condition("summary", "contains", "借款"))
        self.app.sort_details('debit')
        self.root.update()
        self.assertEqual((self.app.detail_sort, self.app.detail_descending), ("debit", True))
        self.assertEqual(self.app.detail_total, 5)
        amounts = [self.view.set(row, "debit") for row in self.view.get_children()]
        self.assertEqual(amounts[:3], ["500.00", "101.00", "100.02"])
        self.app.sort_details('original')
        self.app.sort_details('original')
        self.assertEqual((self.app.detail_sort, self.app.detail_descending), ("original", False))
        self.assertIn("summary", self.app.detail_conditions)


if __name__ == "__main__":
    unittest.main()
