import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import list_marks


class EntryWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = Application()
        self.app = next(iter(self.root.windows))
        self.root.preferences_path = self.base / 'preferences.json'
        self.root.preferences['book_folder'] = str(self.base / 'books')
        self.source = self.base / 'sample.csv'
        self.source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n'
                               '2026-01-01,1,1002,银行存款,收入,1,0\n'
                               '2026-01-01,1,100201,基本户,收入,1,0\n', encoding='utf-8')

    def tearDown(self):
        for app in list(self.root.windows):
            app._close()
        self.temp.cleanup()

    def test_import_creates_book_in_source_directory_without_extra_dialog(self):
        self.root.preferences.pop('book_location_mode', None)
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=()), patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            self.assertIsNone(self.app.store)
            dialog.assert_not_called()
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename') as save, \
             patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            original = self.app.store.path
            self.assertEqual(original.resolve(), (self.base / 'sample.sqlite').resolve())
            dialog.assert_called_once_with(self.app, [self.source])
            self.app.import_file()
            self.assertEqual(self.app.store.path, original)
            second = self.root.new_window()
            second.import_file()
            self.assertEqual(second.store.path.resolve(), (self.base / 'sample-2.sqlite').resolve())
            self.assertTrue(original.exists())
            save.assert_not_called()

    def test_setting_fixed_folder_applies_to_import(self):
        chosen = self.base / 'new-folder'
        with patch('ledger_viewer.ui.filedialog.askdirectory', return_value=str(chosen)):
            self.app.choose_book_folder()
        self.assertEqual(self.root.book_folder, chosen.resolve())
        self.assertEqual(self.root.preferences['book_location_mode'], 'fixed')
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.BatchImportDialog'):
            self.app.import_file()
        self.assertEqual(self.app.store.path.resolve(), (chosen / 'sample.sqlite').resolve())

    def test_unwritable_folder_can_be_changed_or_cancelled(self):
        bad_folder = self.base / 'not-a-folder'
        bad_folder.write_text('preserve')
        self.root.preferences.update(book_location_mode='fixed', book_folder=str(bad_folder))
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.askdirectory', return_value=''), \
             patch('ledger_viewer.ui.messagebox.showerror'), \
             patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            self.assertIsNone(self.app.store)
            dialog.assert_not_called()
        alternative = self.base / 'alternative'
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.askdirectory', return_value=str(alternative)), \
             patch('ledger_viewer.ui.messagebox.showerror'), \
             patch('ledger_viewer.ui.BatchImportDialog'):
            self.app.import_file()
        self.assertEqual(self.app.store.path.resolve(), (alternative / 'sample.sqlite').resolve())
        self.assertEqual(bad_folder.read_text(), 'preserve')

    def test_click_expand_and_collapse_does_not_open_details(self):
        self.app._use_book(self.base / 'book.sqlite')
        header, _, mapping = guess_header(self.source, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(self.source, 'CSV', header, 'entry', mapping))
        self.app.refresh_all()
        self.app.tabs.select(0)
        self.root.update()
        view = self.app.summary_view
        self.assertEqual(view.item('C:1002', 'text'), '展开')
        self.assertFalse(view.bbox('C:100201'))
        x, y, width, height = view.bbox('C:1002', '#0')
        view.event_generate('<Button-1>', x=x + 55, y=y + height // 2)
        view.event_generate('<ButtonRelease-1>', x=x + 55, y=y + height // 2)
        self.root.update()
        self.assertTrue(view.bbox('C:100201'))
        self.assertEqual(view.item('C:1002', 'text'), '收起')
        self.assertEqual(self.app.tabs.index('current'), 0)
        # Direct handler avoids the OS double-click interval changing this second click.
        from types import SimpleNamespace
        self.app.toggle_account(SimpleNamespace(x=x + 55, y=y + height // 2))
        self.root.update()
        self.assertFalse(view.bbox('C:100201'))
        self.assertEqual(view.item('C:1002', 'text'), '展开')
        self.app.toggle_account(SimpleNamespace(x=x + 55, y=y + height // 2))
        self.root.update()
        self.assertTrue(view.bbox('C:100201'))
        # Rebuilding the table (search, source switch…) must keep the opened rows.
        self.app.refresh_all()
        self.root.update()
        self.assertTrue(view.bbox('C:100201'))
        self.assertEqual(view.item('C:1002', 'text'), '收起')
        self.app.set_expansion(True)
        self.assertEqual(view.item('C:1002', 'text'), '收起')
        self.app.remember_expansion()
        self.app.refresh_all()
        self.root.update()
        self.assertTrue(view.bbox('C:100201'))
        view.selection_set('C:1002')
        self.app.open_account(None)
        self.assertEqual(self.app.tabs.index('current'), 1)
        self.assertEqual(len(self.app.detail_view.get_children()), 2)

    def test_vouchers_center_on_current_ledger_on_every_open(self):
        self.app._use_book(self.base / 'book.sqlite')
        header, _, mapping = guess_header(self.source, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(self.source, 'CSV', header, 'entry', mapping))
        self.app.refresh_all()
        entry_id = int(self.app.detail_view.get_children()[0])
        for geometry, edit_attention in [('1000x650+40+40', False),
                                         ('1000x650+180+80', False),
                                         ('1000x650+80+50', True)]:
            self.app.geometry(geometry)
            self.root.update()
            self.app.open_voucher(None, entry_id=entry_id, edit_attention=edit_attention)
            self.root.update()
            dialog = next(iter(self.app.voucher_windows))
            for coordinate, size in [('winfo_rootx', 'winfo_width'), ('winfo_rooty', 'winfo_height')]:
                parent_center = getattr(self.app, coordinate)() + getattr(self.app, size)() / 2
                dialog_center = getattr(dialog, coordinate)() + getattr(dialog, size)() / 2
                self.assertLessEqual(abs(parent_center - dialog_center), 2)
            self.assertEqual(dialog.note_editor.winfo_ismapped(), edit_attention)

    def test_voucher_lookup_stays_compact_until_notes_are_requested(self):
        self.app._use_book(self.base / 'book.sqlite')
        header, _, mapping = guess_header(self.source, 'CSV', 'entry')
        self.app.store.import_sheet(ImportSpec(self.source, 'CSV', header, 'entry', mapping))
        self.app.refresh_all()
        entry_id = int(self.app.detail_view.get_children()[0])

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        def button(dialog, text):
            return next(w for w in widgets(dialog) if isinstance(w, ttk.Button) and w.cget('text') == text)

        self.app.open_voucher(None, entry_id=entry_id)
        self.root.update()
        dialog = next(iter(self.app.voucher_windows))
        editor = next(w for w in widgets(dialog) if isinstance(w, tk.Text) and w.cget("state") == "normal")
        view = next(w for w in widgets(dialog) if isinstance(w, ttk.Treeview))
        hint = next(w for w in widgets(dialog) if isinstance(w, ttk.Label)
                    and w.cget('text') == '按 ESC 键关闭凭证')
        self.assertTrue(hint.winfo_ismapped())
        self.assertLess(hint.winfo_rootx(), dialog.winfo_rootx() + dialog.winfo_width() / 2)
        compact_height = dialog.winfo_height()
        self.assertLess(compact_height, 350)
        self.assertFalse(editor.winfo_ismapped())
        self.assertTrue(view.bbox(view.get_children()[-1]))
        self.assertEqual(list_marks(self.app.store), [])
        position = (dialog.winfo_x(), dialog.winfo_y())
        button(dialog, '关注这张凭证').invoke()
        self.root.update()
        self.assertEqual((dialog.winfo_x(), dialog.winfo_y()), position)
        self.assertTrue(editor.winfo_ismapped())
        self.assertGreater(dialog.winfo_height(), compact_height)
        self.assertEqual(list_marks(self.app.store), [])
        editor.insert('1.0', '待补合同')
        button(dialog, '保存关注与注释').invoke()
        self.assertEqual(list_marks(self.app.store)[0]['note'], '待补合同')
        button(dialog, '收起备注').invoke()
        self.root.update()
        self.assertEqual((dialog.winfo_x(), dialog.winfo_y()), position)
        self.assertFalse(editor.winfo_ismapped())
        self.assertLessEqual(dialog.winfo_height(), compact_height + 2)
        self.app.open_voucher(None, entry_id=entry_id)
        self.root.update()
        self.assertFalse(dialog.winfo_exists())
        dialog = next(iter(self.app.voucher_windows))
        editor = next(w for w in widgets(dialog) if isinstance(w, tk.Text) and w.cget("state") == "normal")
        self.assertFalse(editor.winfo_ismapped())
        self.assertEqual(editor.get('1.0', 'end-1c'), '待补合同')
        button(dialog, '查看 / 编辑关注备注').invoke()
        self.root.update()
        checkbox = next(w for w in widgets(dialog) if isinstance(w, ttk.Checkbutton))
        checkbox.invoke()
        button(dialog, '保存关注与注释').invoke()
        self.assertEqual(list_marks(self.app.store), [])
