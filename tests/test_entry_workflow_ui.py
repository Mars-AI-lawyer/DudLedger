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

    def test_import_creates_book_only_after_selection_and_keeps_existing_books(self):
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=()), patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            self.assertIsNone(self.app.store)
            self.assertFalse(self.root.book_folder.exists())
            dialog.assert_not_called()
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename', side_effect=[str(self.base / 'books' / 'sample.sqlite'), str(self.base / 'books' / 'sample-2.sqlite')]) as save, \
             patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            original = self.app.store.path
            self.assertEqual(original.resolve(), (self.root.book_folder / 'sample.sqlite').resolve())
            self.assertTrue(original.exists())
            dialog.assert_called_once_with(self.app, [self.source])
            self.app.import_file()
            self.assertEqual(self.app.store.path, original)
            second = self.root.new_window()
            second.import_file()
            self.assertEqual(second.store.path.resolve(), (self.root.book_folder / 'sample-2.sqlite').resolve())
            self.assertTrue(original.exists())
            self.assertEqual(save.call_args.kwargs['initialfile'], 'sample-2.sqlite')
        chosen = self.base / 'new-folder'
        with patch('ledger_viewer.ui.filedialog.askdirectory', return_value=str(chosen)):
            self.app.choose_book_folder()
        self.assertEqual(self.root.book_folder, chosen.resolve())
        self.assertIn(str(chosen.resolve()), self.root.preferences_path.read_text())

    def test_cancel_book_location_leaves_no_book(self):
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename', return_value=''), \
             patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
        self.assertIsNone(self.app.store)
        self.assertFalse(list(self.root.book_folder.glob('*.sqlite')))
        dialog.assert_not_called()

    def test_save_dialog_can_change_book_location_without_overwriting(self):
        destination = self.base / '客户资料' / '账套.sqlite'
        destination.parent.mkdir()
        destination.write_text('existing', encoding='utf-8')
        new_book = destination.with_name('账套-新.sqlite')
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), \
             patch('ledger_viewer.ui.filedialog.asksaveasfilename', side_effect=[str(destination), str(new_book)]) as save, \
             patch('ledger_viewer.ui.messagebox.showerror') as error, \
             patch('ledger_viewer.ui.BatchImportDialog'):
            self.app.import_file()
        self.assertEqual(save.call_count, 2)
        error.assert_called_once()
        self.assertEqual(destination.read_text(encoding='utf-8'), 'existing')
        self.assertEqual(self.app.store.path.resolve(), new_book.resolve())
        self.assertEqual(self.root.book_folder.resolve(), destination.parent.resolve())

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
        editor = next(w for w in widgets(dialog) if isinstance(w, tk.Text))
        view = next(w for w in widgets(dialog) if isinstance(w, ttk.Treeview))
        compact_height = dialog.winfo_height()
        self.assertLess(compact_height, 350)
        self.assertFalse(editor.winfo_ismapped())
        self.assertTrue(view.bbox(view.get_children()[-1]))
        self.assertEqual(list_marks(self.app.store), [])
        button(dialog, '关注这张凭证').invoke()
        self.root.update()
        self.assertTrue(editor.winfo_ismapped())
        self.assertGreater(dialog.winfo_height(), compact_height)
        self.assertEqual(list_marks(self.app.store), [])
        editor.insert('1.0', '待补合同')
        button(dialog, '保存关注与注释').invoke()
        self.assertEqual(list_marks(self.app.store)[0]['note'], '待补合同')
        button(dialog, '收起备注').invoke()
        self.root.update()
        self.assertFalse(editor.winfo_ismapped())
        self.assertLessEqual(dialog.winfo_height(), compact_height + 2)
        self.app.open_voucher(None, entry_id=entry_id)
        self.root.update()
        self.assertFalse(dialog.winfo_exists())
        dialog = next(iter(self.app.voucher_windows))
        editor = next(w for w in widgets(dialog) if isinstance(w, tk.Text))
        self.assertFalse(editor.winfo_ismapped())
        self.assertEqual(editor.get('1.0', 'end-1c'), '待补合同')
        button(dialog, '查看 / 编辑关注备注').invoke()
        self.root.update()
        checkbox = next(w for w in widgets(dialog) if isinstance(w, ttk.Checkbutton))
        checkbox.invoke()
        button(dialog, '保存关注与注释').invoke()
        self.assertEqual(list_marks(self.app.store), [])
