"""Focused regression checks for AI voucher notes and sidebar separation."""
import json
import unittest

import test_jev_ui as fixtures
from test_specialist_review import decision_answer
from ledger_viewer.jev_review import ReviewJob, prepare_batch, voucher_results
from ledger_viewer.jev_ui import ReviewDetail
from ledger_viewer.voucher_notes import list_marks


class ReviewDetailLayoutTest(unittest.TestCase):
    setUp = fixtures.ReviewUITest.setUp
    tearDown = fixtures.ReviewUITest.tearDown

    def detail(self, geometry='840x560'):
        batch = prepare_batch(self.app.store, {'review_accounts': ['其他应付款'], 'bailian_free_confirmed': True})
        def post(key, payload, endpoint):
            return {'model': 'decision-model-preview', 'answers': {key: decision_answer(key == 'OP01') for key in payload['questions']},
                'usage': {'input_tokens': 10}}
        job = ReviewJob(self.app.store.path, batch, '', bailian_key='fictional-key', bailian_post=post)
        job.start(self.app.store)
        job.thread.join(5)
        self.assertFalse(job.thread.is_alive())
        self.panel.batch_id = batch
        dialog = ReviewDetail(self.panel, voucher_results(self.app.store, batch)[0])
        dialog.geometry(geometry)
        self.root.update()
        return dialog

    def assert_inside(self, widget, dialog):
        self.assertTrue(widget.winfo_ismapped())
        x = widget.winfo_rootx() - dialog.winfo_rootx()
        y = widget.winfo_rooty() - dialog.winfo_rooty()
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + widget.winfo_width(), dialog.winfo_width() + 1)
        self.assertLessEqual(y + widget.winfo_height(), dialog.winfo_height() + 1)

    def test_notes_receive_space_and_save_without_scrolling(self):
        dialog = self.detail()
        for coordinate, size in [('winfo_rootx', 'winfo_width'), ('winfo_rooty', 'winfo_height')]:
            parent_center = getattr(self.app, coordinate)() + getattr(self.app, size)() / 2
            dialog_center = getattr(dialog, coordinate)() + getattr(dialog, size)() / 2
            self.assertLessEqual(abs(parent_center - dialog_center), 2)
        dialog.note_toggle.invoke()
        self.root.update()
        self.assertGreater(dialog.note.winfo_height(), 160)
        self.assertGreater(dialog.note.winfo_height(), dialog.voucher_view.winfo_height())
        self.assertLess(dialog.voucher_view.winfo_height(), 190)
        last_row = dialog.voucher_view.bbox(dialog.voucher_view.get_children()[-1])
        self.assertTrue(last_row)
        self.assertLessEqual(last_row[1] + last_row[3], dialog.voucher_view.winfo_height())
        self.assert_inside(dialog.note_save_button, dialog)
        self.assert_inside(dialog.close_button, dialog)
        text = '需要补充借款合同、审批记录及期后还款情况。\n' * 12
        dialog.note.insert('1.0', text)
        dialog.note_save_button.invoke()
        self.root.update()
        self.assertEqual(list_marks(self.app.store)[0]['note'], text)
        self.assert_inside(dialog.note_save_button, dialog)
        dialog.request_close()

    def test_minimum_size_keeps_editor_and_primary_actions_visible(self):
        dialog = self.detail('640x420')
        dialog.note_toggle.invoke()
        self.root.update()
        self.assertGreaterEqual(dialog.note.winfo_height(), 90)
        for widget in (dialog.note, dialog.note_save_button, dialog.close_button, dialog.note_toggle):
            self.assert_inside(widget, dialog)
        dialog.request_close()

    def test_json_and_advice_do_not_take_space_from_notes(self):
        dialog = self.detail('640x420')
        dialog.note_toggle.invoke()
        self.root.update()
        original_height = dialog.note.winfo_height()
        dialog.note.insert('1.0', '保留这条备注')
        dialog._toggle_json()
        self.root.update()
        self.assertLessEqual(abs(dialog.note.winfo_height() - original_height), 3)
        self.assertEqual(dialog.note.get('1.0', 'end-1c'), '保留这条备注')
        self.assert_inside(dialog.note_save_button, dialog)
        dialog._toggle_json()
        dialog.preview_tabs.select(1)
        self.root.update()
        self.assert_inside(dialog.note_save_button, dialog)
        self.assertEqual(dialog.note.get('1.0', 'end-1c'), '保留这条备注')
        dialog.request_close()
        self.assertEqual(list_marks(self.app.store)[0]['note'], '保留这条备注')

    def test_reopening_notes_preserves_draft_and_split_is_adjustable(self):
        dialog = self.detail()
        dialog.note_toggle.invoke()
        self.root.update()
        original = dialog.detail_panes.sashpos(0)
        dialog.detail_panes.sashpos(0, original + 30)
        self.root.update()
        self.assertEqual(dialog.detail_panes.sashpos(0), original + 30)
        dialog.note.insert('1.0', '关注备注不会丢失')
        dialog.note_toggle.invoke()
        self.root.update()
        self.assertFalse(dialog.note_save_button.winfo_ismapped())
        dialog.note_toggle.invoke()
        self.root.update()
        self.assertEqual(dialog.note.get('1.0', 'end-1c'), '关注备注不会丢失')
        self.assert_inside(dialog.note_save_button, dialog)
        dialog.request_close()

    def test_sidebar_separates_reports_and_functions_in_both_modes(self):
        self.app.geometry('1000x700')
        for expanded in (False, True):
            if self.app._sidebar_expanded != expanded:
                self.app._toggle_sidebar()
            self.root.update()
            reports, marks = self.app.nav_buttons[2:4]
            gap = marks.winfo_rooty() - reports.winfo_rooty() - reports.winfo_height()
            self.assertGreaterEqual(gap, 24)
            divider = self.app.sidebar_section_divider
            self.assertTrue(divider.winfo_ismapped())
            self.assertGreaterEqual(divider.winfo_rooty(), reports.winfo_rooty() + reports.winfo_height())
            self.assertLessEqual(divider.winfo_rooty() + divider.winfo_height(), marks.winfo_rooty())
        self.assertEqual([heading.cget('text') for heading, button in self.app.sidebar_groups], ['账簿', '核查'])


if __name__ == '__main__':
    unittest.main()
