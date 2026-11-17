import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from ledger_viewer import issue_store as issues
from ledger_viewer.importer import classify_sheet
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import list_marks, remove_mark, voucher_key


class IssueWorkbenchUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        with patch('ledger_viewer.ui.Path.home', return_value=self.base):
            self.root = Application()
        self.root.set_font_size(13, persist=False)
        self.app = next(iter(self.root.windows))
        self.app.geometry('1160x690+40+40')
        self.app._use_book(self.base / '工作台.sqlite')
        path = self.base / '分录.csv'
        path.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                        '2026-01-02,1,1002,银行存款,股东出资,200,0\n'
                        '2026-01-02,1,4001,实收资本,确认出资,0,200\n'
                        '2026-01-03,2,2241,其他应付款,往来款,100,0\n', encoding='utf-8')
        self.app.store.import_sheet(classify_sheet(path, 'CSV'))
        self.app.refresh_all()
        self.entry = self.app.store.entries()[0]
        self.key = voucher_key(self.entry)
        self.second_key = voucher_key(self.app.store.entries()[-1])
        self.issue = issues.create_issue(self.app.store, '出资问题')
        self.other = issues.create_issue(self.app.store, '往来问题')
        self.panel = self.app.workbench
        self.app.select_page('entries')
        self.panel.show()
        self.panel.edit_issue(self.issue)
        self.root.update()

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def wait_for_autosave(self):
        done = tk.BooleanVar(value=False)
        self.root.after(900, lambda: done.set(True))
        self.root.wait_variable(done)

    def mark_binding(self):
        self.app.refresh_marks(select_key=self.key)
        self.app.select_page('marks')
        self.root.update()
        return next(item for item in self.app.note_hub.items.values() if item['editor'] is self.app.mark_note)

    def test_explicit_join_button_uses_checked_group(self):
        self.app.detail_selection.checked.update((self.key, self.second_key))
        self.app.detail_selection.refresh()
        with patch.object(self.panel, 'choose_issue', side_effect=lambda keys: self.panel.add_to_issue(self.issue, keys)):
            self.app.detail_join.invoke()
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 2)
        self.assertEqual(len(self.app.detail_selection.checked), 2)

    def test_join_actions_from_marks_and_voucher_context(self):
        self.panel.add_to_issue(self.issue, [self.key, self.second_key])
        self.app.refresh_marks()
        self.app.select_page('marks')
        self.root.update()
        self.app.mark_selection.checked.update((self.key, self.second_key))
        self.app.mark_selection.refresh()
        with patch.object(self.panel, 'choose_issue', side_effect=lambda keys: self.panel.add_to_issue(self.other, keys)):
            self.app.mark_join.invoke()
        self.assertEqual(issues.list_issues(self.app.store)[1]['count'], 2)
        self.app.open_voucher(None, entry_id=self.entry['id'])
        dialog = next(iter(self.app.voucher_windows))
        self.root.update()
        tools = dialog.cell_tools
        tools.select(tools.view.get_children()[0], 'debit')
        self.assertEqual(tuple(map(str, tools.view.cget('show'))), ('headings',))
        with patch.object(self.panel, 'choose_issue') as choose:
            self.app.voucher_actions.choose_from_view(tools.view)
        self.assertEqual(choose.call_args.args[0], (self.key,))

    def test_cancelled_issue_chooser_does_not_write(self):
        self.app.detail_selection.checked.add(self.key)
        self.app.detail_selection.refresh()
        self.app.detail_join.invoke()
        self.root.update()
        chooser = next(child for child in self.app.winfo_children()
                       if isinstance(child, tk.Toplevel) and child.title() == '加入尽调问题')
        chooser.destroy()
        self.assertEqual(list_marks(self.app.store), [])
        self.assertEqual(issues.issue_vouchers(self.app.store, self.issue), [])
        self.assertIsNone(self.app.grab_current())

    def test_drag_uses_explicit_handles_without_capturing_cell_selection(self):
        self.assertFalse(hasattr(self.app, 'drag'))
        for view in (self.app.detail_view, self.app.mark_view, self.panel.voucher_view):
            self.assertIn('⋮⋮', view.heading('#0', 'text'))
            self.assertTrue(any(str(tag).startswith('VoucherDrag:') for tag in view.bindtags()))
        view = self.app.detail_view
        box = view.bbox(str(self.entry['id']), 'summary')
        view.event_generate('<ButtonPress-1>', x=box[0] + 20, y=box[1] + 10)
        view.event_generate('<B1-Motion>', x=400, y=300)
        view.event_generate('<ButtonRelease-1>', x=400, y=300)
        self.assertIsNone(self.app.grab_current())
        self.assertEqual(issues.issue_vouchers(self.app.store, self.issue), [])

    def test_workbench_vouchers_join_other_issue_without_losing_original_link(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.panel.voucher_selection.checked.add(self.key)
        self.panel.voucher_selection.refresh()
        with patch.object(self.panel, 'choose_issue', side_effect=lambda keys: self.panel.add_to_issue(self.other, keys)):
            self.app.voucher_actions.choose_from_view(self.panel.voucher_view)
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [1, 1])
        self.assertEqual(self.panel.issue_id, self.issue)

    def test_back_button_save_failure_keeps_editor_in_detail(self):
        self.panel.notes.insert('end', '保存失败不能离开')
        with patch('ledger_viewer.issue_store.save_issue', side_effect=OSError('保存失败')):
            self.panel.back_button.invoke()
            self.assertEqual(self.panel.view_mode, 'detail')
            self.assertIn('保存失败不能离开', self.panel.notes.get('1.0', 'end-1c'))
        self.assertTrue(self.panel.flush())


    def test_single_selection_does_not_load_vouchers_or_create_draft(self):
        self.panel.reset()
        self.panel.show()
        self.root.update()
        with patch.object(self.panel, 'load_issue') as loaded:
            self.panel.tree.selection_set(f'i:{self.other}')
            self.panel.tree.focus(f'i:{self.other}')
            self.root.update()
            self.assertEqual(self.panel.selected_issue_id, self.other)
            self.assertIsNone(self.panel.issue_id)
            self.assertIsNone(self.panel.draft)
            self.assertEqual(self.panel.view_mode, 'directory')
            self.panel.tree.focus_set()
            self.panel.tree.event_generate('<Up>')
            self.root.update()
            self.assertEqual(self.panel.selected_issue_id, self.issue)
            self.assertIsNone(self.panel.draft)
            loaded.assert_not_called()

    def test_double_click_opens_details_and_enter_opens_selected_problem(self):
        self.panel.back_to_directory()
        self.root.update()
        box = self.panel.tree.bbox(f'i:{self.other}', 'title')
        for stamp in (10000, 10070):
            self.panel.tree.event_generate('<ButtonPress-1>', x=box[0] + 30, y=box[1] + 10, time=stamp)
            self.panel.tree.event_generate('<ButtonRelease-1>', x=box[0] + 30, y=box[1] + 10, time=stamp + 10)
        self.root.update()
        self.assertEqual(self.panel.view_mode, 'detail')
        self.assertEqual(self.panel.issue_id, self.other)
        self.assertEqual(self.panel.detail_tab, 'vouchers')
        self.assertEqual(self.app.current_page(), 'entries')
        self.panel.back_to_directory()
        self.panel.tree.selection_set(f'i:{self.issue}')
        self.panel.tree.focus_set()
        self.root.update()
        self.panel.tree.event_generate('<Return>')
        self.root.update()
        self.assertEqual(self.panel.issue_id, self.issue)
        self.assertEqual(self.panel.view_mode, 'detail')

    def test_back_restores_directory_search_selection_and_scroll(self):
        self.panel.back_to_directory()
        for index in range(50):
            issues.create_issue(self.app.store, f'核查事项{index:02d}')
        self.panel.search.set('核查事项')
        self.root.update()
        self.panel.tree.yview_moveto(.5)
        self.root.update()
        row = self.panel.tree.identify_row(70)
        self.assertTrue(row)
        self.panel.tree.selection_set(row)
        self.root.update()
        position = self.panel.tree.yview()[0]
        self.panel._open_selected_issue()
        self.root.update()
        self.assertTrue(self.panel.back_to_directory())
        self.root.update()
        self.assertEqual(self.panel.search.get(), '核查事项')
        self.assertEqual(self.panel.tree.selection(), (row,))
        self.assertAlmostEqual(self.panel.tree.yview()[0], position, delta=.025)

    def test_new_issue_and_batch_add_stay_in_directory_without_loading(self):
        self.panel.back_to_directory()
        with patch.object(self.panel, 'load_issue') as loaded:
            with patch('ledger_viewer.workbench.simpledialog.askstring', return_value='新增问题'):
                self.panel.new_issue()
            new_id = self.panel.selected_issue_id
            self.assertNotEqual(new_id, self.issue)
            self.assertEqual(self.panel.view_mode, 'directory')
            self.assertEqual(self.panel.tree.set(f'i:{new_id}', 'count'), '0')
            self.panel.add_to_issue(new_id, [self.key, self.second_key])
            self.assertEqual(self.panel.view_mode, 'directory')
            self.assertEqual(self.panel.tree.set(f'i:{new_id}', 'count'), '2')
            self.panel.add_to_issue(new_id, self.key)
            self.assertEqual(self.panel.tree.set(f'i:{new_id}', 'count'), '2')
            loaded.assert_not_called()

    def test_detail_tabs_each_use_full_height_and_default_to_vouchers(self):
        self.root.update()
        self.assertTrue(self.panel.vouchers_page.winfo_ismapped())
        self.assertFalse(self.panel.record_page.winfo_ismapped())
        self.panel.detail_tabs.select(self.panel.record_page)
        self.root.update()
        self.assertEqual(self.panel.detail_tab, 'record')
        self.assertTrue(self.panel.record_page.winfo_ismapped())
        self.assertFalse(self.panel.voucher_view.winfo_ismapped())
        self.assertGreater(self.panel.canvas.winfo_height(), self.panel.details_body.winfo_height() * .65)
        self.panel.back_to_directory()
        self.panel.edit_issue(self.issue)
        self.root.update()
        self.assertEqual(self.panel.detail_tab, 'vouchers')
        self.assertGreater(self.panel.voucher_view.winfo_height(), self.panel.details_body.winfo_height() * .5)

    def test_failed_save_blocks_back_and_tab_switch(self):
        self.panel.detail_tabs.select(self.panel.record_page)
        self.root.update()
        self.panel.notes.insert('end', '未保存草稿')
        with patch('ledger_viewer.issue_store.save_issue', side_effect=OSError('磁盘写入失败')):
            self.assertFalse(self.panel.back_to_directory())
            self.panel.detail_tabs.select(self.panel.vouchers_page)
            self.root.update()
            self.assertEqual(self.panel.detail_tabs.select(), str(self.panel.record_page))
            self.assertEqual(self.panel.view_mode, 'detail')
            self.assertIn('未保存草稿', self.panel.notes.get('1.0', 'end'))
        self.assertTrue(self.panel.flush())

    def test_reopening_workbench_starts_at_directory(self):
        self.panel.hide()
        self.panel.show()
        self.root.update()
        self.assertEqual(self.panel.view_mode, 'directory')
        self.assertTrue(self.panel.directory_body.winfo_ismapped())
        self.assertFalse(self.panel.details_body.winfo_ismapped())

    def test_rehost_preserves_detail_tab_or_directory_selection(self):
        self.panel.detail_tabs.select(self.panel.record_page)
        self.root.update()
        self.panel.notes.insert('end', '切换宿主保留')
        self.panel.detach()
        self.root.update()
        self.assertEqual(self.panel.view_mode, 'detail')
        self.assertEqual(self.panel.detail_tab, 'record')
        self.assertEqual(self.panel.notes.get('1.0', 'end-1c'), '切换宿主保留')
        self.panel.back_to_directory()
        self.panel.tree.selection_set(f'i:{self.other}')
        self.root.update()
        self.panel.dock()
        self.root.update()
        self.assertEqual(self.panel.view_mode, 'directory')
        self.assertEqual(self.panel.selected_issue_id, self.other)
        self.assertEqual(self.panel.tree.selection(), (f'i:{self.other}',))
        self.assertEqual(self.panel.issue_id, self.issue)


    def test_issue_autosave_and_page_switch_preserve_current_problem(self):
        self.panel.notes.insert('end', '核对银行流水')
        self.wait_for_autosave()
        self.assertEqual(issues.get_issue(self.app.store, self.issue)['notes'], '核对银行流水')
        self.app.select_page('marks')
        self.root.update()
        self.assertEqual(self.panel.current, ('issue', self.issue))
        self.assertTrue(self.panel.issues_visible)

    def test_failed_autosave_blocks_hide_detach_switch_and_exit(self):
        self.panel.notes.insert('end', '保留输入')
        with patch('ledger_viewer.issue_store.save_issue', side_effect=OSError('磁盘写入失败')):
            self.assertFalse(self.panel.hide())
            self.assertFalse(self.panel.detach())
            self.assertFalse(self.panel.edit_issue(self.other))
            self.app.close_book()
            self.assertIsNotNone(self.app.store)
            self.assertIn('保留输入', self.panel.notes.get('1.0', 'end'))
        self.assertTrue(self.panel.flush())

    def test_selecting_voucher_does_not_replace_issue_editor(self):
        self.panel.add_to_issue(self.issue, self.key)
        row = next(iter(self.panel.rows))
        self.panel.voucher_view.selection_set(row)
        self.root.update()
        self.assertEqual(self.panel.current, ('issue', self.issue))
        self.assertFalse(hasattr(self.panel, 'preview_target'))
        self.assertEqual(self.panel.voucher_view.set(row, 'amount'), '200.00')
        self.assertIn('确认出资', self.panel.voucher_view.set(row, 'summary'))

    def test_batch_link_duplicate_multilink_and_unmark_are_independent(self):
        self.assertTrue(self.panel.add_to_issue(self.issue, [self.key, self.second_key, self.key]))
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 2)
        self.assertTrue(self.panel.add_to_issue(self.issue, self.key))
        self.assertIn('已存在 1 张', self.panel.feedback.get())
        self.panel.add_to_issue(self.other, self.key)
        remove_mark(self.app.store, self.key)
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [2, 1])
        self.panel.remove_link(self.issue, self.key)
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [1, 1])


    def test_notes_sync_without_overwriting_dirty_other_editor(self):
        self.panel.add_to_issue(self.issue, self.key)
        binding = self.mark_binding()
        self.app.open_voucher(None, entry_id=self.entry['id'], edit_attention=True)
        dialog = next(iter(self.app.voucher_windows))
        dialog.note_editor.insert('end', '共享备注')
        self.assertTrue(dialog.note_draft.flush())
        self.assertEqual(binding['editor'].get('1.0', 'end-1c'), '共享备注')
        dialog.note_editor.insert('end', '，窗口新输入')
        binding['editor'].insert('end', '，关注页新输入')
        self.assertTrue(binding['draft'].flush())
        self.assertFalse(dialog.note_draft.flush())
        self.assertIn('窗口新输入', dialog.note_editor.get('1.0', 'end'))
        dialog.note_draft.baseline = dialog.note_draft.capture()

    def test_note_sync_preserves_pending_unmark_in_other_window(self):
        self.panel.add_to_issue(self.issue, self.key)
        binding = self.mark_binding()
        self.app.open_voucher(None, entry_id=self.entry['id'], edit_attention=True)
        dialog = next(iter(self.app.voucher_windows))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        checkbox = next(w for w in descendants(dialog) if isinstance(w, ttk.Checkbutton))
        checkbox.invoke()
        binding['editor'].insert('end', '来自关注页')
        self.assertTrue(binding['draft'].flush())
        self.assertFalse(self.root.tk.getboolean(dialog.getvar(checkbox.cget('variable'))))
        self.assertTrue(dialog.request_close())
        self.assertEqual(list_marks(self.app.store), [])
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 1)

    def test_note_conflict_recovery_preserves_local_input(self):
        self.panel.add_to_issue(self.issue, self.key)
        binding = self.mark_binding()
        binding['editor'].insert('end', '本地输入')
        with self.app.store.db:
            self.app.store.db.execute("UPDATE voucher_marks SET note='其他入口的新备注' WHERE voucher_key=?", (self.key,))
        self.assertFalse(binding['draft'].flush())
        with patch('ledger_viewer.workbench.messagebox.askyesno', return_value=True):
            self.assertTrue(self.app.note_hub.reload(binding['editor']))
        self.assertEqual(self.root.clipboard_get(), '本地输入')
        self.assertEqual(binding['editor'].get('1.0', 'end-1c'), '其他入口的新备注')

    def test_issue_conflict_recovery_preserves_draft(self):
        self.panel.notes.insert('end', '当前草稿')
        issues.save_issue(self.app.store, self.issue, 0, '出资问题', '待核查', '', '另一份笔记')
        self.assertFalse(self.panel.flush())
        with patch('ledger_viewer.workbench.messagebox.askyesno', return_value=True):
            self.panel.reload_issue(self.issue)
        self.assertIn('当前草稿', self.root.clipboard_get())
        self.assertEqual(self.panel.notes.get('1.0', 'end-1c'), '另一份笔记')


    def test_detach_dock_and_close_restore_issue_and_saved_notes(self):
        self.panel.notes.insert('end', '保存后展开')
        self.assertTrue(self.panel.detach())
        self.root.update()
        self.assertTrue(self.panel.detached)
        self.assertEqual(self.panel.notes.get('1.0', 'end-1c'), '保存后展开')
        self.assertFalse(self.panel.visible)
        self.assertTrue(self.panel.issues_visible)
        self.panel.hide_issues()
        self.root.update()
        self.assertFalse(self.panel.issues_visible)
        self.panel.show()
        self.root.update()
        self.assertTrue(self.panel.issues_visible)
        self.assertTrue(self.panel.dock())
        self.root.update()
        self.assertFalse(self.panel.detached)
        self.assertEqual(self.panel.issue_id, self.issue)

    def test_small_window_uses_overlay_without_detaching(self):
        self.app.geometry('820x520')
        self.root.set_font_size(18, persist=False)
        self.root.update()
        self.assertFalse(self.panel.detached)
        self.assertTrue(self.panel.overlay)
        self.assertTrue(self.panel.issues_visible)
        self.assertGreaterEqual(self.app.detail_view.winfo_width(), 520)
        self.assertTrue(self.app.workbench_button.winfo_ismapped())

    def test_overlay_keeps_unsaved_editor_accessible(self):
        self.panel.notes.insert('end', '不可丢弃的笔记')
        with patch('ledger_viewer.issue_store.save_issue', side_effect=OSError('保存失败')):
            self.app.geometry('820x520')
            self.root.update()
            self.assertFalse(self.panel.detached)
            self.assertTrue(self.panel.issues_visible)
            self.assertIn('不可丢弃', self.panel.notes.get('1.0', 'end'))
        self.assertTrue(self.panel.flush())

    def test_unlink_removes_a_pinned_row_from_the_table(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.panel.remove_link(self.issue, self.key)
        self.assertFalse(self.panel.rows)
        self.assertEqual(self.panel.voucher_view.get_children(), ())

    def test_directory_uses_full_height_without_editor_or_horizontal_scroll(self):
        self.panel.back_to_directory()
        for index in range(30):
            issues.create_issue(self.app.store, f'核查问题{index:02d}')
        self.panel.refresh_directory()
        self.root.update()
        self.assertTrue(self.app.workbench_button.instate(['selected']))
        self.assertEqual(tuple(self.panel.tree['columns']), ('title', 'count'))
        self.assertTrue(self.panel.directory_body.winfo_ismapped())
        self.assertFalse(self.panel.details_body.winfo_ismapped())
        self.assertFalse(self.panel.voucher_view.winfo_ismapped())
        self.assertGreater(self.panel.tree.winfo_height(), self.panel.issue_body.winfo_height() * .65)
        self.assertEqual(self.panel.tree.cget('xscrollcommand'), '')
        visible = {self.panel.tree.identify_row(y) for y in range(0, self.panel.tree.winfo_height(), 4)} - {''}
        self.assertGreaterEqual(len(visible), 10)
        self.root.set_font_size(18, persist=False)
        self.root.update()
        visible = {self.panel.tree.identify_row(y) for y in range(0, self.panel.tree.winfo_height(), 4)} - {''}
        self.assertGreaterEqual(len(visible), 7)

    def test_calculator_can_coexist_with_detached_workbench(self):
        self.panel.detach()
        self.app.detail_tools.select(str(self.entry['id']), 'debit')
        self.app.detail_tools.calculate(1)
        self.app.calculator_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.mode, 'calculator')
        self.assertTrue(self.panel.issues_visible)
        self.assertEqual(self.app.detail_tools.result.get(), '200.00')
        self.assertTrue(self.app.sidebar.winfo_ismapped())

    def test_directory_paginates_and_pins_distant_duplicate(self):
        issues.link_voucher(self.app.store, self.issue, self.key)
        ref = self.app.store.db.execute('SELECT * FROM dd_voucher_refs').fetchone()
        with self.app.store.db:
            for number in range(3, 207):
                key = json.dumps(['period', '2026', '1', '', str(number)], separators=(',', ':'))
                self.app.store.db.execute('INSERT INTO dd_voucher_refs VALUES(?,?,?,?,?)', (key, ref['label'], ref['summary'], ref['signature'], ref['snapshot_json']))
                self.app.store.db.execute('INSERT INTO dd_issue_vouchers(issue_id,voucher_key) VALUES(?,?)', (self.issue, key))
        self.panel.refresh_directory()
        with patch('ledger_viewer.issue_store.voucher_preview', side_effect=AssertionError('列表不读取逐张预览')):
            self.panel.load_issue(self.issue)
        self.assertEqual(len(self.panel.rows), 100)
        self.panel.add_to_issue(self.issue, key)
        self.assertEqual(len(self.panel.rows), 101)
        self.panel.load_issue(self.issue, more=True)
        self.panel.load_issue(self.issue, more=True)
        self.assertEqual(len(set(self.panel.rows.values())), 205)

    def test_missing_voucher_opens_readonly_snapshot(self):
        self.panel.add_to_issue(self.issue, self.key)
        with self.app.store.db:
            self.app.store.db.execute('DELETE FROM entries')
        self.panel.load_issue(self.issue)
        row = next(iter(self.panel.rows))
        self.assertIn('原凭证未匹配', self.panel.voucher_view.set(row, 'status'))
        with patch.object(self.panel, 'open_snapshot') as opened:
            self.panel.open_key(self.key)
            self.assertIsNone(opened.call_args.args[0]['entry_id'])
            self.assertIn('分录.csv', opened.call_args.args[0]['lines'][0]['filename'])
