import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ledger_viewer.table_layout import WorkspaceTableFit, readable_minimum
from ledger_viewer.workbench import RightWorkbench
from ledger_viewer.workbench_support import (
    VoucherDragSession,
    drag_handle_at,
    link_feedback,
    ordered_issue_choices,
    use_overlay_drawer,
)


class FakeView:
    def __init__(self, widths, visible_width):
        self.widths = dict(widths)
        self.minima = {key: 60 for key in widths}
        self.visible_width = visible_width
        self.scroll_calls = []

    def bind(self, *_args, **_kwargs):
        pass

    def cget(self, key):
        return {'displaycolumns': ('#all',), 'columns': tuple(self.widths),
                'show': ('headings',)}[key]

    def column(self, key, option=None, **changes):
        if 'width' in changes:
            self.widths[key] = changes['width']
        return self.minima[key] if option == 'minwidth' else self.widths[key]

    def winfo_ismapped(self):
        return True

    def winfo_width(self):
        return self.visible_width

    def xview(self):
        return (.25, .75)

    def xview_moveto(self, position):
        self.scroll_calls.append(position)


class TableLayoutHeadlessTest(unittest.TestCase):
    def fixture(self, visible_width=400):
        view = FakeView({'summary': 280, 'counterparty': 200, 'debit': 140, 'date': 100}, visible_width)
        app = SimpleNamespace(workbench=SimpleNamespace(visible=True), after_idle=lambda callback: callback)
        return WorkspaceTableFit(app, [view]), view, app

    def test_key_columns_have_different_reading_minima(self):
        self.assertEqual(readable_minimum('summary', 60), 220)
        self.assertEqual(readable_minimum('counterparty', 60), 160)
        self.assertEqual(readable_minimum('debit', 60), 112)
        self.assertEqual(readable_minimum('date', 60), 60)

    def test_narrow_table_keeps_horizontal_overflow_and_restores_widths(self):
        fitter, view, app = self.fixture()
        original = dict(view.widths)
        fitter.fit()
        self.assertEqual(view.widths, {'summary': 220, 'counterparty': 160, 'debit': 112, 'date': 60})
        self.assertGreater(sum(view.widths.values()), view.visible_width)
        self.assertEqual(view.scroll_calls, [])
        app.workbench.visible = False
        fitter.fit()
        self.assertEqual(view.widths, original)
        self.assertEqual(view.scroll_calls, [.25])

    def test_overlay_restores_instead_of_compressing_the_ledger(self):
        fitter, view, app = self.fixture()
        original = dict(view.widths)
        fitter.fit()
        app.workbench.overlay = True
        fitter.fit()
        self.assertEqual(view.widths, original)
        self.assertEqual(view.scroll_calls, [.25])

    def test_original_widths_stay_unchanged_when_they_fit(self):
        fitter, view, _app = self.fixture(800)
        original = dict(view.widths)
        fitter.fit()
        self.assertEqual(view.widths, original)
        self.assertEqual(view.scroll_calls, [0])


class WorkbenchSupportTest(unittest.TestCase):
    def test_narrow_issue_panel_uses_overlay_without_detaching(self):
        self.assertTrue(use_overlay_drawer(700, 525))
        self.assertFalse(use_overlay_drawer(1000, 525))
        self.assertFalse(use_overlay_drawer(700, 525, detached=True))
        self.assertFalse(use_overlay_drawer(700, 525, mode='calculator'))
        self.assertFalse(use_overlay_drawer(1, 525))

    def test_issue_chooser_pins_current_then_recent_and_filters(self):
        items = [
            {'id': 1, 'title': '出资', 'status': '待核查'},
            {'id': 2, 'title': '往来', 'status': '核查中'},
            {'id': 3, 'title': '收入', 'status': '待核查'},
            {'id': 4, 'title': '费用', 'status': '已完成'},
        ]
        ordered = ordered_issue_choices(items, current_id=3, recent_ids=[2, 3])
        self.assertEqual([item['id'] for item in ordered], [3, 2, 1, 4])
        filtered = ordered_issue_choices(items, '往来', current_id=3, recent_ids=[2, 3])
        self.assertEqual([item['id'] for item in filtered], [2])
        self.assertEqual(ordered_issue_choices(items, '已完成')[0]['id'], 4)

    def test_completion_feedback_names_target_and_all_counts(self):
        text = link_feedback('出资问题', {'added': 2, 'duplicates': 1, 'attention': 2})
        for expected in ('出资问题', '新增 2 张', '重复', '同步关注 2 张', '已有备注未覆盖'):
            self.assertIn(expected, text)

    def test_drag_handle_does_not_claim_checkbox_or_data_cells(self):
        self.assertFalse(drag_handle_at('#0', 20, 'tree'))
        self.assertFalse(drag_handle_at('summary', 40, 'cell'))
        self.assertFalse(drag_handle_at('#0', 40, 'separator'))
        self.assertTrue(drag_handle_at('#0', 40, 'tree'))


class WorkbenchActionHeadlessTest(unittest.TestCase):
    def test_nav_state_leaves_count_and_text_to_app_presentation(self):
        app = SimpleNamespace(update_workbench_presentation=Mock(),
                              workbench_button=Mock(), calculator_button=Mock())
        panel = SimpleNamespace(app=app, detached=False, visible=True, mode='issues')
        RightWorkbench._nav_state(panel)
        app.update_workbench_presentation.assert_called_once_with()
        app.workbench_button.configure.assert_not_called()

    def test_add_feedback_counts_only_actual_new_attention_records(self):
        marks = {'existing': '原备注'}
        def note_record(_store, key):
            return {'note': marks[key]} if key in marks else None
        def link(_store, _issue_id, keys):
            for key in keys:
                marks.setdefault(key, '')
            return {'added': 2, 'duplicates': 0}
        panel = SimpleNamespace(app=SimpleNamespace(store=object()), flush=lambda: True,
                                _after_add=Mock())
        with patch('ledger_viewer.workbench.data.note_record', side_effect=note_record), \
                patch('ledger_viewer.workbench.data.link_vouchers', side_effect=link):
            self.assertTrue(RightWorkbench.add_to_issue(panel, 7, ('new', 'existing')))
        result = panel._after_add.call_args.args[2]
        self.assertEqual(result['attention'], 1)
        self.assertEqual(marks['existing'], '原备注')


class VoucherDragSessionTest(unittest.TestCase):
    def fixture(self):
        commits, presentations, finishes = [], [], []
        def resolve(x, _y, _keys):
            return {'issue_id': 7, 'title': '目标问题', 'added': 1, 'duplicates': 1} if x > 0 else None
        session = VoucherDragSession(resolve, lambda target, keys: commits.append((target, keys)) or True,
                                     lambda *args: presentations.append(args), finishes.append)
        return session, commits, presentations, finishes

    def test_invalid_drop_and_escape_never_write(self):
        session, commits, presentations, finishes = self.fixture()
        self.assertTrue(session.start(('a', 'a', 'b'), 0, 0))
        self.assertEqual(presentations[0][0], ('a', 'b'))
        self.assertFalse(session.drop(0, 0))
        self.assertEqual(commits, [])
        session.start(('a', 'b'), 1, 0)
        self.assertTrue(session.cancel())
        self.assertEqual(commits, [])
        self.assertEqual(finishes, [False, False])

    def test_failed_commit_cleans_up_and_restores_target_selection(self):
        session, _commits, _presentations, finishes = self.fixture()
        session.commit = lambda _target, _keys: False
        session.start(('a',), 1, 0)
        self.assertFalse(session.drop(1, 0))
        self.assertEqual(finishes, [False])
        self.assertFalse(session.active)
        self.assertEqual(session.keys, ())

    def test_commit_exception_cleans_up_restores_and_propagates(self):
        session, _commits, _presentations, finishes = self.fixture()
        session.commit = Mock(side_effect=RuntimeError('write failed'))
        session.start(('a',), 1, 0)
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            session.drop(1, 0)
        self.assertEqual(finishes, [False])
        self.assertFalse(session.active)
        self.assertEqual(session.keys, ())

    def test_valid_drop_previews_duplicates_and_commits_once(self):
        session, commits, presentations, finishes = self.fixture()
        session.start(('a', 'b'), 1, 0)
        self.assertEqual(presentations[-1][1]['duplicates'], 1)
        self.assertTrue(session.drop(1, 0))
        self.assertEqual(commits[0][0]['issue_id'], 7)
        self.assertEqual(commits[0][1], ('a', 'b'))
        self.assertFalse(session.drop(1, 0))
        self.assertEqual(len(commits), 1)
        self.assertEqual(finishes, [True])


if __name__ == '__main__':
    unittest.main()
