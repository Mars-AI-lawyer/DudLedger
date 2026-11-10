import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        self.errors = []
        self.root.report_callback_exception = lambda kind, value, traceback: self.errors.append(value)
        self.app = next(iter(self.root.windows))
        self.app._use_book(self.base / '工作台.sqlite')
        source = self.base / '分录.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                          '2026-01-02,1,1002,银行存款,股东出资,200,0\n'
                          '2026-01-02,1,4001,实收资本,股东出资,0,200\n'
                          '2026-01-03,2,2241,其他应付款,股东往来,100,0\n', encoding='utf-8')
        self.app.store.import_sheet(classify_sheet(source, 'CSV'))
        self.app.refresh_all()
        self.app.tabs.select(1)
        self.panel = self.app.workbench
        self.issue = issues.create_issue(self.app.store, '出资问题')
        self.other = issues.create_issue(self.app.store, '往来问题')
        self.entry = self.app.store.entries()[0]
        self.key = voucher_key(self.entry)
        self.panel.show()
        self.root.update()

    def tearDown(self):
        self.app.drag.cancel()
        if self.panel.draft:
            self.panel.draft.baseline = self.panel.draft.capture()
        for binding in list(getattr(self.app.note_hub, 'items', {}).values()):
            binding['draft'].baseline = binding['draft'].capture()
        self.app._close()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def wait_for_autosave(self):
        ready = tk.BooleanVar(value=False)
        self.root.after(950, lambda: ready.set(True))
        self.root.wait_variable(ready)
        self.root.update()

    def test_issue_autosave_and_directory_stays_visible(self):
        self.panel.edit_issue(self.issue)
        self.panel.notes.insert('end', '核对银行流水及股东出资凭据')
        self.panel.status_var.set('待补资料')
        self.wait_for_autosave()
        saved = issues.get_issue(self.app.store, self.issue)
        self.assertEqual(saved['status'], '待补资料')
        self.assertEqual(saved['notes'], '核对银行流水及股东出资凭据')
        self.assertTrue(self.panel.tree.winfo_ismapped())
        self.app.tabs.select(4)
        self.root.update()
        self.assertTrue(self.panel.winfo_ismapped())
        self.assertEqual(self.panel.current, ('issue', self.issue))

    def test_failed_autosave_blocks_switch_hide_and_exit(self):
        self.panel.edit_issue(self.issue)
        self.panel.notes.insert('end', '保留输入')
        with patch('ledger_viewer.issue_store.save_issue', side_effect=OSError('磁盘写入失败')):
            self.assertFalse(self.panel.hide())
            self.assertFalse(self.panel.preview(self.key))
            self.app.close_book()
            self.assertIsNotNone(self.app.store)
            self.assertIn('保留输入', self.panel.notes.get('1.0', 'end'))
            self.assertIn('保存失败', self.panel.feedback.get())
        self.assertTrue(self.panel.flush())

    def test_preview_does_not_link_and_regular_selection_does_not_replace(self):
        self.assertTrue(self.panel.preview(self.key))
        self.assertEqual(list_marks(self.app.store), [])
        self.assertEqual(issues.issue_vouchers(self.app.store, self.issue), [])
        row = self.app.detail_view.get_children()[-1]
        self.app.detail_view.selection_set(row)
        self.root.update()
        self.assertEqual(self.panel.current[1], self.key)

    def test_add_duplicate_multilink_and_unmark_preserve_issues(self):
        self.assertTrue(self.panel.add_to_issue(self.issue, self.key))
        self.assertTrue(self.panel.add_to_issue(self.issue, self.key))
        self.assertEqual(self.panel.feedback.get(), '该问题已包含此凭证')
        self.assertTrue(self.panel.add_to_issue(self.other, self.key))
        remove_mark(self.app.store, self.key)
        self.app.note_hub.broadcast(self.key)
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [1, 1])
        self.panel.remove_link(self.issue, self.key)
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [0, 1])
        self.assertEqual(list_marks(self.app.store), [])

    def drag_to(self, view, row, target):
        self.root.update()
        box = view.bbox(row, '#0')
        self.assertTrue(box)
        x, y = box[0]+12, box[1]+box[3]//2
        view.event_generate('<ButtonPress-1>', x=x, y=y)
        self.assertIsNotNone(self.app.drag.pending)
        destination = SimpleNamespace(widget=view, x_root=target[0], y_root=target[1])
        self.app.drag.motion(destination)
        self.assertTrue(self.app.drag.active)
        self.app.drag.release(destination)
        self.root.update()

    def issue_target(self, issue_id):
        self.panel.tree.see(f'i:{issue_id}')
        self.root.update()
        box = self.panel.tree.bbox(f'i:{issue_id}')
        return self.panel.tree.winfo_rootx()+box[0]+80, self.panel.tree.winfo_rooty()+box[1]+box[3]//2

    def test_real_handle_binding_and_drop_from_detail(self):
        row = str(self.entry['id'])
        self.drag_to(self.app.detail_view, row, self.issue_target(self.issue))
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 1)
        self.assertEqual(len(self.panel.preview_item['lines']), 2)
        self.assertEqual(self.app.detail_tools.selection_range(), ((), ()))

    def test_drop_from_mark_and_attached_voucher_window(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.app.refresh_marks()
        self.app.tabs.select(4)
        self.root.update()
        self.drag_to(self.app.mark_view, self.app.mark_view.get_children()[0], self.issue_target(self.other))
        self.assertEqual(issues.list_issues(self.app.store)[1]['count'], 1)
        # Drag across a real Tk toplevel into the owning book's workbench.
        entry = self.app.store.entries()[-1]
        self.app.open_voucher(None, entry_id=entry['id'])
        dialog = next(iter(self.app.voucher_windows))
        dialog.geometry('650x350+20+50')
        self.app.geometry('1160x690+100+100')
        self.root.update()
        view = dialog.cell_tools.view
        self.drag_to(view, view.get_children()[0], self.issue_target(self.issue))
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 2)

    def test_cancel_drag_and_preview_drop_do_not_write(self):
        view = self.app.detail_view
        row = str(self.entry['id'])
        box = view.bbox(row, '#0')
        view.event_generate('<ButtonPress-1>', x=box[0]+10, y=box[1]+10)
        event = SimpleNamespace(widget=view, x_root=self.panel.preview_target.winfo_rootx()+30,
                                y_root=self.panel.preview_target.winfo_rooty()+10)
        self.app.drag.motion(event)
        view.event_generate('<Escape>')
        self.root.update()
        self.assertFalse(self.app.drag.active)
        self.assertEqual(list_marks(self.app.store), [])
        self.drag_to(view, row, (event.x_root, event.y_root))
        self.assertEqual(self.panel.current[1], self.key)
        self.assertEqual(list_marks(self.app.store), [])

    def test_notes_sync_without_overwriting_dirty_other_editor(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.app.open_voucher(None, entry_id=self.entry['id'], edit_attention=True)
        dialog = next(iter(self.app.voucher_windows))
        self.root.update()
        dialog.note_editor.insert('end', '共享备注')
        self.assertTrue(dialog.note_draft.flush())
        bindings = list(self.app.note_hub.items.values())
        preview_binding = next(item for item in bindings if item['editor'] is not dialog.note_editor)
        self.assertEqual(preview_binding['editor'].get('1.0', 'end-1c'), '共享备注')
        dialog.note_editor.insert('end', '，窗口新输入')
        preview_binding['editor'].insert('end', '，侧栏新输入')
        self.assertTrue(preview_binding['draft'].flush())
        self.assertFalse(dialog.note_draft.flush())
        self.assertIn('窗口新输入', dialog.note_editor.get('1.0', 'end'))
        self.assertIn('侧栏新输入', list_marks(self.app.store)[0]['note'])
        # Resolve for cleanup; the conflict itself never discards either value.
        dialog.note_draft.baseline = dialog.note_draft.capture()

    def test_note_sync_preserves_pending_unmark_choice_in_other_window(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.app.open_voucher(None, entry_id=self.entry['id'], edit_attention=True)
        dialog = next(iter(self.app.voucher_windows))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        checkbox = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Checkbutton))
        checkbox.invoke()
        preview = next(item for item in self.app.note_hub.items.values() if item['editor'] is not dialog.note_editor)
        preview['editor'].insert('end', '来自侧栏的新备注')
        self.assertTrue(preview['draft'].flush())
        self.assertFalse(self.root.tk.getboolean(dialog.getvar(checkbox.cget('variable'))))
        self.assertTrue(dialog.request_close())
        self.assertEqual(list_marks(self.app.store), [])
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 1)

    def test_note_conflict_recovery_copies_local_input_and_loads_saved_note(self):
        self.panel.add_to_issue(self.issue, self.key)
        binding = next(iter(self.app.note_hub.items.values()))
        binding['editor'].insert('end', '本地尚未保存的输入')
        with self.app.store.db:
            self.app.store.db.execute("UPDATE voucher_marks SET note='其他入口的新备注' WHERE voucher_key=?", (self.key,))
        self.assertFalse(binding['draft'].flush())
        with patch('ledger_viewer.workbench.messagebox.askyesno', return_value=True):
            self.assertTrue(self.app.note_hub.reload(binding['editor']))
        self.assertEqual(self.root.clipboard_get(), '本地尚未保存的输入')
        self.assertEqual(binding['editor'].get('1.0', 'end-1c'), '其他入口的新备注')
        self.assertTrue(self.panel.flush())

    def test_issue_conflict_recovery_preserves_draft_on_clipboard(self):
        self.panel.edit_issue(self.issue)
        self.panel.notes.insert('end', '当前草稿')
        issues.save_issue(self.app.store, self.issue, 0, '出资问题', '待核查', '', '另一份已保存笔记')
        self.assertFalse(self.panel.flush())
        with patch('ledger_viewer.workbench.messagebox.askyesno', return_value=True):
            self.panel.reload_issue(self.issue)
        self.assertIn('当前草稿', self.root.clipboard_get())
        self.assertEqual(self.panel.notes.get('1.0', 'end-1c'), '另一份已保存笔记')
        self.assertTrue(self.panel.flush())

    def test_edge_hover_opens_closed_panel_and_escape_restores_it(self):
        self.panel.hide()
        view = self.app.detail_view
        self.root.update()
        box = view.bbox(str(self.entry['id']), '#0')
        view.event_generate('<ButtonPress-1>', x=box[0]+10, y=box[1]+10)
        self.app.drag.motion(SimpleNamespace(widget=view, x_root=self.app.winfo_rootx()+self.app.winfo_width()-5,
                                            y_root=self.app.winfo_rooty()+200))
        ready = tk.BooleanVar(value=False)
        self.root.after(550, lambda: ready.set(True))
        self.root.wait_variable(ready)
        self.assertTrue(self.panel.visible)
        self.app.drag.cancel()
        self.root.update()
        self.assertFalse(self.panel.visible)
        self.assertEqual(list_marks(self.app.store), [])

    def test_drag_between_issues_adds_reference_without_removing_source(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.root.update()
        row = next(row for row, pair in self.panel.rows.items() if pair == (self.issue, self.key))
        box = self.panel.tree.bbox(row)
        self.panel.tree.event_generate('<ButtonPress-1>', x=box[0]+35, y=box[1]+box[3]//2)
        self.assertIsNotNone(self.app.drag.pending)
        x, y = self.issue_target(self.other)
        event = SimpleNamespace(widget=self.panel.tree, x_root=x, y_root=y)
        self.app.drag.motion(event)
        self.app.drag.release(event)
        self.root.update()
        self.assertEqual([item['count'] for item in issues.list_issues(self.app.store)], [1, 1])

    def test_rebinding_mark_editor_does_not_save_to_previous_voucher(self):
        second = self.app.store.entries()[-1]
        second_key = voucher_key(second)
        issues.link_voucher(self.app.store, self.issue, self.key)
        issues.link_voucher(self.app.store, self.issue, second_key)
        self.app.refresh_marks()
        self.app.tabs.select(4)
        self.root.update()
        first_row = next(row for row, key in self.app.mark_keys.items() if key == self.key)
        second_row = next(row for row, key in self.app.mark_keys.items() if key == second_key)
        for row in (first_row, second_row, first_row, second_row):
            self.app.mark_view.selection_set(row)
            self.root.update()
        self.app.mark_note.insert('end', '仅第二张凭证')
        self.wait_for_autosave()
        notes = {item['key']: item['note'] for item in list_marks(self.app.store)}
        self.assertEqual(notes[self.key], '')
        self.assertEqual(notes[second_key], '仅第二张凭证')

    def test_locate_outside_current_filters_and_page(self):
        with self.app.store.db:
            for number in range(510):
                self.app.store.db.execute("""INSERT INTO entries(source_id,row_no,entry_date,fiscal_year,period,voucher_no,
                    voucher_type,account_key,summary,counterparty,auxiliary,debit_cents,credit_cents,raw_json)
                    SELECT source_id,?,entry_date,fiscal_year,period,?,voucher_type,account_key,summary,
                    counterparty,auxiliary,debit_cents,credit_cents,raw_json FROM entries WHERE id=?""",
                    (100+number, str(1000+number), self.entry['id']))
        entry_id = self.app.store.entries()[-1]['id']
        self.app.search.set('不存在的词')
        self.app.submit_detail_search()
        self.root.update()
        self.assertEqual(self.app.detail_total, 0)
        with patch('ledger_viewer.ui.messagebox.askyesno', return_value=False):
            self.app.locate_issue_voucher(voucher_key(self.app.store.entry(entry_id)))
        self.assertEqual(self.app.detail_total, 0)
        with patch('ledger_viewer.ui.messagebox.askyesno', return_value=True):
            self.app.locate_issue_voucher(voucher_key(self.app.store.entry(entry_id)))
        self.root.update()
        self.assertEqual(self.app.detail_view.selection(), (str(entry_id),))
        self.assertEqual(self.app.detail_search, '')
        self.assertEqual(self.app.detail_offset, 500)

    def test_directory_paginates_and_distant_match_does_not_load_every_snapshot(self):
        import json
        issues.link_voucher(self.app.store, self.issue, self.key)
        ref = self.app.store.db.execute('SELECT * FROM dd_voucher_refs').fetchone()
        with self.app.store.db:
            for number in range(3, 207):
                key = json.dumps(['period', '2026', '1', '', str(number)], separators=(',', ':'))
                self.app.store.db.execute('INSERT INTO dd_voucher_refs VALUES(?,?,?,?,?)',
                                          (key, ref['label'], ref['summary'], ref['signature'], ref['snapshot_json']))
                self.app.store.db.execute('INSERT INTO dd_issue_vouchers(issue_id,voucher_key) VALUES(?,?)', (self.issue, key))
        last_key = key
        self.panel.refresh_directory()
        with patch('ledger_viewer.issue_store.voucher_preview', side_effect=AssertionError('目录不应加载快照')):
            self.panel.load_issue(self.issue)
        self.assertEqual(len(self.panel.rows), 100)
        self.assertTrue(self.panel.tree.exists(f'more:{self.issue}'))
        self.assertTrue(self.panel.add_to_issue(self.issue, last_key))
        self.assertEqual(len(self.panel.rows), 101)
        self.panel.load_issue(self.issue, more=True)
        self.panel.load_issue(self.issue, more=True)
        self.assertEqual(len(set(self.panel.rows.values())), 205)
        self.assertFalse(self.panel.tree.exists(f'more:{self.issue}'))

    def test_missing_reference_preview_retains_source_and_disables_live_actions(self):
        self.panel.add_to_issue(self.issue, self.key)
        self.panel.edit_issue(self.issue)
        with self.app.store.db:
            self.app.store.db.execute('DELETE FROM entries')
        self.assertTrue(self.panel.preview(self.key, self.issue))
        self.assertIsNone(self.panel.preview_item['entry_id'])
        self.assertIn('原凭证未匹配', self.panel.preview_item['status'])
        self.assertIn('分录.csv', self.panel.lines_text.get('1.0', 'end'))
        self.assertEqual(issues.list_issues(self.app.store)[0]['count'], 1)

    def test_calculator_contexts_small_overlay_and_large_font(self):
        self.app.detail_tools.select(str(self.entry['id']), 'debit')
        self.app.detail_tools.calculate(1)
        self.app.detail_tools.toggle()
        self.root.update()
        self.assertEqual(self.panel.mode, 'calculator')
        self.assertEqual(self.app.detail_tools.result.get(), '200.00')
        self.app.tabs.select(0)
        self.root.update()
        self.assertIs(self.panel.active_tools, self.app.balance_tools)
        self.app.tabs.select(4)
        self.root.update()
        self.assertIs(self.panel.active_tools, self.app.balance_tools)
        self.panel.show()
        self.app.geometry('820x520')
        self.root.set_font_size(18, persist=False)
        self.root.update()
        self.assertEqual(self.panel.winfo_manager(), 'place')
        self.assertGreaterEqual(self.app.detail_view.winfo_width(), 500)
        self.assertLessEqual(self.panel.winfo_width(), self.app.content.winfo_width())
        self.panel.hide()
        self.root.update()
        self.assertFalse(self.panel.winfo_ismapped())
        self.assertEqual(self.app.detail_tools.result.get(), '200.00')
