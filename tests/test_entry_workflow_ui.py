import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.ui import Application


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
        with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(self.source),)), patch('ledger_viewer.ui.BatchImportDialog') as dialog:
            self.app.import_file()
            original = self.app.store.path
            self.assertEqual(original, self.root.book_folder / 'sample.sqlite')
            self.assertTrue(original.exists())
            dialog.assert_called_once_with(self.app, [self.source])
            self.app.import_file()
            self.assertEqual(self.app.store.path, original)
            second = self.root.new_window()
            second.import_file()
            self.assertEqual(second.store.path, self.root.book_folder / 'sample-2.sqlite')
            self.assertTrue(original.exists())
        chosen = self.base / 'new-folder'
        with patch('ledger_viewer.ui.filedialog.askdirectory', return_value=str(chosen)):
            self.app.choose_book_folder()
        self.assertEqual(self.root.book_folder, chosen.resolve())
        self.assertIn(str(chosen.resolve()), self.root.preferences_path.read_text())

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
