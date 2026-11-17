"""Behavior checks without creating Tk windows or calling external services."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ledger_viewer.account_navigation import AccountNavigation
from ledger_viewer.detail_filter_ui import MonthSelection, heading_filter_column, month_label
from ledger_viewer.detail_filters import make_condition
from ledger_viewer.importer import classify_sheet
from ledger_viewer.review_rules_ui import ReviewRulesDialog
from ledger_viewer.shortcuts import (VoucherShortcuts, configured_shortcuts, event_shortcut,
                                     normalize_shortcut, validate_shortcuts)
from ledger_viewer.specialist_rules import load_rules
from ledger_viewer.store import LedgerStore
from ledger_viewer.ui import SettingsDialog


class ShortcutTest(unittest.TestCase):
    def fixture(self, page='entries', widget_class='Treeview'):
        app = Mock()
        app.store = True
        app.controller.preferences = {}
        app.grab_current.return_value = None
        app.tk.call.return_value = 'aqua'
        app.current_page.return_value = page
        app.voucher_actions.sources = {app.detail_view: lambda row: 'current'}
        app.voucher_actions.keys_from_view.return_value = ('one', 'two')
        app.detail_view.winfo_class.return_value = widget_class
        app.detail_view.winfo_toplevel.return_value = app
        return app, VoucherShortcuts(app), SimpleNamespace(widget=app.detail_view, keysym='F', state=1)

    def test_default_actions_and_live_preference_changes(self):
        app, shortcuts, event = self.fixture()
        self.assertEqual(shortcuts.dispatch(event), 'break')
        app.mark_voucher_keys.assert_called_once_with(('one', 'two'))
        event.keysym = 'G'
        shortcuts.dispatch(event)
        app.workbench.choose_issue.assert_called_once_with(('one', 'two'))
        app.controller.preferences['voucher_shortcuts'] = {'mark': 'Ctrl+M', 'join': 'Alt+J'}
        app.mark_voucher_keys.reset_mock()
        event.keysym, event.state = 'F', 1
        self.assertIsNone(shortcuts.dispatch(event))
        event.keysym, event.state = 'm', 4
        shortcuts.dispatch(event)
        app.mark_voucher_keys.assert_called_once_with(('one', 'two'))

    def test_text_fields_modals_and_unrelated_dialogs_do_not_trigger(self):
        for name in ('Entry', 'TEntry', 'Text', 'TCombobox', 'Spinbox'):
            app, shortcuts, event = self.fixture(widget_class=name)
            self.assertIsNone(shortcuts.dispatch(event))
            app.mark_voucher_keys.assert_not_called()
        app, shortcuts, event = self.fixture()
        app.grab_current.return_value = object()
        self.assertIsNone(shortcuts.dispatch(event))
        app.mark_voucher_keys.assert_not_called()
        app.grab_current.return_value = None
        event.widget.winfo_toplevel.return_value = SimpleNamespace()
        self.assertIsNone(shortcuts.dispatch(event))
        app.mark_voucher_keys.assert_not_called()

    def test_open_voucher_uses_its_own_key_even_without_a_selected_row(self):
        app, shortcuts, event = self.fixture()
        top = SimpleNamespace(voucher_view=object(), voucher_key='opened-voucher')
        event.widget.winfo_toplevel.return_value = top
        shortcuts.dispatch(event)
        app.mark_voucher_keys.assert_called_once_with(('opened-voucher',))
        app.voucher_actions.keys_from_view.assert_not_called()

    def test_marks_and_empty_selection_follow_current_scope(self):
        app, shortcuts, event = self.fixture(page='marks')
        widget = Mock()
        widget.winfo_class.return_value = 'TButton'
        widget.winfo_toplevel.return_value = app
        event.widget, event.keysym = widget, 'G'
        shortcuts.dispatch(event)
        app.voucher_actions.keys_from_view.assert_called_once_with(app.mark_view)
        app.workbench.choose_issue.assert_called_once_with(('one', 'two'))
        app.workbench.choose_issue.reset_mock()
        app.voucher_actions.keys_from_view.return_value = ()
        shortcuts.dispatch(event)
        app.workbench.choose_issue.assert_not_called()

    def test_shortcut_recording_rejects_duplicates_and_saves_a_valid_combination(self):
        editor = SimpleNamespace(shortcut_vars={'mark': Mock(), 'join': Mock()},
                                 shortcut_buttons={'mark': Mock(), 'join': Mock()},
                                 shortcut_status=Mock(), tk=Mock(), bind=Mock(), _stop_shortcut_recording=Mock())
        editor.shortcut_vars['mark'].get.return_value = 'Shift+F'
        editor.shortcut_vars['join'].get.return_value = 'Shift+G'
        editor.tk.call.return_value = 'aqua'
        SettingsDialog._record_shortcut(editor, 'mark')
        capture = editor.bind.call_args.args[1]
        self.assertEqual(capture(SimpleNamespace(keysym='G', state=1)), 'break')
        editor.shortcut_vars['mark'].set.assert_not_called()
        self.assertEqual(capture(SimpleNamespace(keysym='m', state=4)), 'break')
        editor.shortcut_vars['mark'].set.assert_called_once_with('Ctrl+M')
        self.assertEqual(capture(SimpleNamespace(keysym='Escape', state=0)), 'break')

    def test_normalization_conflicts_and_platform_modifiers(self):
        self.assertEqual(normalize_shortcut(' shift + control + f '), 'Ctrl+Shift+F')
        for values in ({'mark': 'Shift+F', 'join': 'shift+f'},
                       {'mark': 'Cmd+W', 'join': 'Shift+G'},
                       {'mark': 'F', 'join': 'Shift+G'}):
            with self.assertRaises(ValueError):
                validate_shortcuts(values)
        self.assertEqual(configured_shortcuts({'voucher_shortcuts': None}), {'mark': 'Shift+F', 'join': 'Shift+G'})
        self.assertEqual(event_shortcut(SimpleNamespace(keysym='f', state=9), True), 'Shift+Cmd+F')
        self.assertEqual(event_shortcut(SimpleNamespace(keysym='g', state=16), True), 'Alt+G')
        self.assertEqual(event_shortcut(SimpleNamespace(keysym='g', state=8), False), 'Alt+G')


class MonthAndColumnTest(unittest.TestCase):
    def test_month_choices_survive_year_switches_and_clear_only_current_year(self):
        model = MonthSelection(('2015-06', '2015-07', '2016-01', '2016-06'))
        model.choose('2015-06', True)
        model.choose('2015-07', True)
        model.choose_year('2016', True)
        model.choose_year('2016', False)
        self.assertEqual(model.condition().values, ('2015-06', '2015-07'))
        self.assertEqual(month_label('2015-06'), '2015年6月')
        model.choose_year('2015', False)
        self.assertIsNone(model.condition())
        model.choose('2019-01', True)
        self.assertIsNone(model.condition())

    def test_only_relevant_headings_open_filters_and_separators_stay_draggable(self):
        view = Mock()
        view.identify_region.return_value = 'heading'
        view.identify_column.return_value = '#2'
        for column in ('date', 'summary', 'debit', 'credit', 'original', 'name', 'counterparty', 'period', 'maker'):
            view.column.return_value = column
            expected = column if column in ('date', 'summary', 'debit', 'credit', 'original') else None
            self.assertEqual(heading_filter_column(view, 50, 10), expected)
        view.identify_region.return_value = 'separator'
        self.assertIsNone(heading_filter_column(view, 50, 10))

    def test_summary_and_month_amount_filters_keep_paging_totals_and_export_consistent(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'fictional.csv'
            source.write_text('日期,凭证号,科目编码,科目名称,摘要,往来单位,借方金额,贷方金额\n'
                '2015-06-01,1,1002,银行存款,借款,甲,100,0\n'
                '2015-07-31,2,1002,银行存款,借款归还,甲,200,0\n'
                '2015-07-01,3,1002,银行存款,正常结算,借款,300,0\n'
                '2015-08-01,4,1002,银行存款,借款,甲,400,0\n'
                '2016-06-01,5,1002,银行存款,借款,甲,500,0\n', encoding='utf-8')
            store = LedgerStore(base / 'book.sqlite')
            self.addCleanup(store.close)
            store.import_sheet(classify_sheet(source, 'CSV'))
            model = MonthSelection(store.entry_months())
            model.choose('2015-06', True)
            model.choose('2015-07', True)
            conditions = (model.condition(), make_condition('summary', 'contains', '借款'), make_condition('debit', 'gt', '50'))
            rows, total, offset = store.entries_page(conditions=conditions, page_size=1)
            self.assertEqual((total, offset, rows[0]['voucher_no']), (2, 0, '1'))
            next_rows, next_total, _ = store.entries_page(conditions=conditions, page_size=1, offset=1)
            self.assertEqual((next_total, next_rows[0]['voucher_no']), (2, '2'))
            output = base / 'export.csv'
            store.export_entries(output, conditions=conditions)
            with output.open(encoding='utf-8-sig') as handle:
                exported = list(csv.DictReader(handle))
            self.assertEqual([row['凭证号'] for row in exported], ['1', '2'])
            self.assertEqual(sum(row['debit_cents'] for row in store.entries(conditions=conditions)), 30000)


class NavigationAndRulesTest(unittest.TestCase):
    def test_tree_indicator_and_collapse_do_not_change_selected_scope(self):
        tree = Mock()
        tree.identify_element.return_value = 'Treeitem.indicator'
        tree.identify_row.return_value = 'parent'
        tree.get_children.return_value = ('child',)
        tree.item.return_value = False
        nav = SimpleNamespace(tree=tree, expanded=set(), current='child', parents={'child': 'parent'})
        self.assertEqual(AccountNavigation._toggle_indicator(nav, SimpleNamespace(x=5, y=5)), 'break')
        self.assertEqual(nav.expanded, {'parent'})
        tree.selection_set.assert_not_called()
        AccountNavigation.set_expansion(nav, False)
        self.assertEqual(nav.current, 'child')
        self.assertEqual(nav.expanded, set())
        tree.item.assert_called_with('parent', open=False)

    def test_saving_simplified_editor_keeps_existing_advice_and_information_gap_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LedgerStore(Path(folder) / 'book.sqlite')
            self.addCleanup(store.close)
            version, rules = load_rules(store)
            original_advice, original_insufficient = rules['topics'][0]['advice'], rules['insufficient']
            editor = SimpleNamespace(rules=rules, store=store, _stash=Mock(), destroy=Mock(), app=Mock(), common_editor=Mock())
            editor.common_editor.get.return_value = '所有模型共同使用的新边界。'
            ReviewRulesDialog._save(editor)
            new_version, saved = load_rules(store)
            self.assertGreater(new_version, version)
            self.assertEqual(saved['common'], '所有模型共同使用的新边界。')
            self.assertEqual(saved['topics'][0]['advice'], original_advice)
            self.assertEqual(saved['insufficient'], original_insufficient)


if __name__ == '__main__':
    unittest.main()
