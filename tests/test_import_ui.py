import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch
from types import SimpleNamespace

from ledger_viewer.ui import Application, ImportDialog, BatchImportDialog


class ImportDialogTest(unittest.TestCase):
    def test_jev_action_is_visible_near_the_header_at_large_font(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.csv"
            path.write_text("科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n", encoding="utf-8")
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            try:
                app.set_font_size(20, persist=False)
                dialog = ImportDialog(next(iter(app.windows)), path)
                app.update()
                self.assertTrue(dialog.jev_button.winfo_ismapped())
                self.assertLess(dialog.jev_button.winfo_rooty(), dialog.map_canvas.winfo_rooty())
                self.assertLess(
                    dialog.jev_button.winfo_rooty() + dialog.jev_button.winfo_height(),
                    app.winfo_screenheight() - 20,
                )
                buttons = [
                    child for frame in dialog.winfo_children()
                    if isinstance(frame, ttk.Frame)
                    for child in frame.winfo_children()
                    if isinstance(child, ttk.Button) and child.cget("text") == "确认并导入"
                ]
                self.assertEqual(len(buttons), 1)
                self.assertTrue(buttons[0].winfo_ismapped())
                dialog.destroy()
            finally:
                app.destroy()

    def test_unified_import_preserves_per_sheet_mapping_and_jev_suggestions(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            known = base / '正常.csv'
            unknown = base / '自定义.csv'
            known.write_text('科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n', encoding='utf-8')
            unknown.write_text('编号,名称,增加,减少\n2241,其他应付款,0,10\n', encoding='utf-8')
            app = Application()
            window = next(iter(app.windows))
            try:
                window._use_book(base / 'book.sqlite')
                with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(known), str(unknown))):
                    window.import_file()
                dialog = next(w for w in window.winfo_children() if isinstance(w, BatchImportDialog))
                self.assertEqual(len(dialog.items), 2)
                self.assertEqual(dialog.selected, {0})
                dialog.view.selection_set('1')
                dialog._edit_current()
                editor = next(w for w in window.winfo_children() if isinstance(w, ImportDialog))
                app.update()
                self.assertTrue(editor.jev_button.winfo_ismapped())
                suggestion = SimpleNamespace(kind='entry', header_row=1,
                    mapping={'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}, warnings=[], model='fake')
                with patch('ledger_viewer.ui.get_api_key', return_value='fake-key'), \
                     patch('ledger_viewer.ui.messagebox.askokcancel', return_value=True), \
                     patch('ledger_viewer.ui.suggest_mapping', return_value=suggestion) as suggest, \
                     patch('ledger_viewer.ui.messagebox.showinfo'):
                    editor._offer_jev()
                    # Wait for the simulated response without sending any data.
                    import time
                    deadline = time.monotonic() + 3
                    while str(editor.jev_button['state']) == 'disabled' and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                    suggest.assert_called_once()
                editor.import_now()
                self.assertEqual(window.store.sources(), [])
                self.assertEqual(dialog.selected, {0, 1})
                self.assertEqual(dialog.items[1][2].mapping['credit'], 3)
                # Reopening must preserve the approved mapping, not overwrite with guesses.
                dialog._edit_current()
                editor = next(w for w in window.winfo_children() if isinstance(w, ImportDialog))
                self.assertEqual(editor.vars['credit'].get(), editor.columns[3])
                editor.destroy()
                with patch('ledger_viewer.ui.messagebox.showinfo'):
                    dialog._import_selected()
                self.assertEqual(len(window.store.sources()), 2)
                rows = window.store.db.execute('SELECT debit_cents,credit_cents FROM entries ORDER BY id').fetchall()
                self.assertEqual([tuple(row) for row in rows], [(1000, 0), (0, 1000)])
            finally:
                window._close()


if __name__ == "__main__":
    unittest.main()
