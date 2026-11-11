"""Isolated Tk import fixtures: no network, credentials or clipboard changes."""

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.importer import guess_mapping
from ledger_viewer.jev_mapping import MappingSuggestion
from ledger_viewer.ui import Application, BatchImportDialog


class ImportUiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.key = patch('ledger_viewer.ui.deepseek_get_api_key', return_value='fake-key').start()
        self.addCleanup(patch.stopall)
        self.suggest = patch('ledger_viewer.ui.deepseek_suggest_mapping', side_effect=self.suggestion).start()
        try:
            with patch('ledger_viewer.ui.Path.home', return_value=self.base):
                self.root = Application()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.window = next(iter(self.root.windows))
        self.window._use_book(self.base / 'book.sqlite')
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.source = self.csv('sample.csv')
        self.addCleanup(self.close_app)

    def close_app(self):
        self.window._close()
        self.assertEqual(self.callback_errors, [])

    def csv(self, name, header='科目编码,科目名称,借方金额,贷方金额', data='1002,银行存款,10,0'):
        path = self.base / name
        path.write_text(header + '\n' + data + '\n', encoding='utf-8')
        return path

    @staticmethod
    def suggestion(preview, api_key):
        row = max(preview['rows'], key=lambda item: len(guess_mapping(item['cells'], 'entry')) + (4 if item['cells'] == ['编号', '名称', '增加', '减少'] else 0))
        kind = 'balance' if any('期末' in str(cell) for cell in row['cells']) else 'entry'
        mapping = guess_mapping(row['cells'], kind)
        if row['cells'] == ['编号', '名称', '增加', '减少']:
            mapping = {'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}
        return MappingSuggestion(kind, row['row'], mapping, {}, (), 'fake-model')

    def dialog(self, paths=None):
        self.batch = BatchImportDialog(self.window, paths or [self.source])
        return self.batch

    def wait_for(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.root.update()
            if predicate():
                self.assertEqual(self.callback_errors, [])
                return
            time.sleep(.005)
        self.fail('Background operation did not finish')

    def wait_review(self, dialog):
        self.wait_for(lambda: not dialog.ai_running and not dialog.working and
                      not dialog.tasks.busy('inventory'))
