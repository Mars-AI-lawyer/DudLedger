import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch
from types import SimpleNamespace

from ledger_viewer.ui import Application, ImportDialog, BatchImportDialog


class ImportDialogTest(unittest.TestCase):
    def test_ai_action_is_visible_near_the_header_at_large_font(self):
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
                self.assertTrue(dialog.ai_button.winfo_ismapped())
                self.assertLess(dialog.ai_button.winfo_rooty(), dialog.map_canvas.winfo_rooty())
                self.assertLess(
                    dialog.ai_button.winfo_rooty() + dialog.ai_button.winfo_height(),
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

    def test_unified_import_preserves_per_sheet_mapping_and_deepseek_suggestions(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            known = base / '正常.csv'
            unknown = base / '自定义.csv'
            known.write_text('科目编码,科目名称,借方金额,贷方金额\n1002,银行存款,10,0\n', encoding='utf-8')
            unknown.write_text('编号,名称,增加,减少\n2241,其他应付款,0,10\n', encoding='utf-8')
            app = Application()
            window = next(iter(app.windows))
            app.ai_provider = 'deepseek'
            try:
                window._use_book(base / 'book.sqlite')
                with patch('ledger_viewer.ui.filedialog.askopenfilenames', return_value=(str(known), str(unknown))):
                    window.import_file()
                dialog = next(w for w in window.winfo_children() if isinstance(w, BatchImportDialog))
                self.assertEqual(len(dialog.items), 2)
                self.assertEqual(dialog.selected, {0, 1})
                dialog.view.selection_set('1')
                dialog._edit_current()
                editor = next(w for w in window.winfo_children() if isinstance(w, ImportDialog))
                app.update()
                self.assertTrue(editor.ai_button.winfo_ismapped())
                suggestion = SimpleNamespace(kind='entry', header_row=1,
                    mapping={'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}, warnings=[], model='fake')
                with patch('ledger_viewer.ui.deepseek_get_api_key', return_value='fake-key'), \
                     patch('ledger_viewer.ui.messagebox.askokcancel', return_value=True), \
                     patch('ledger_viewer.ui.deepseek_suggest_mapping', return_value=suggestion) as suggest, \
                     patch('ledger_viewer.ui.messagebox.showinfo'):
                    editor._offer_ai()
                    # Wait for the simulated response without sending any data.
                    import time
                    deadline = time.monotonic() + 3
                    while str(editor.ai_button['state']) == 'disabled' and time.monotonic() < deadline:
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
                with patch('ledger_viewer.ui.messagebox.showinfo'), patch('ledger_viewer.ui.deepseek_suggest_mapping') as suggest:
                    dialog.use_ai.set(False)
                    dialog._import_selected()
                    suggest.assert_not_called()
                self.assertEqual(len(window.store.sources()), 2)
                rows = window.store.db.execute('SELECT debit_cents,credit_cents FROM entries ORDER BY id').fetchall()
                self.assertEqual([tuple(row) for row in rows], [(1000, 0), (0, 1000)])
            finally:
                window._close()

    def test_default_deepseek_applies_to_every_sheet_after_one_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            first = base / '一.csv'
            second = base / '二.csv'
            first.write_text('编号,名称,增加,减少\n1002,银行存款,10,0\n', encoding='utf-8')
            second.write_text('编号,名称,增加,减少\n2241,其他应付款,0,10\n', encoding='utf-8')
            app = Application()
            window = next(iter(app.windows))
            app.ai_provider = 'deepseek'
            try:
                window._use_book(base / 'book.sqlite')
                dialog = BatchImportDialog(window, [first, second])
                self.assertTrue(dialog.use_ai.get())
                self.assertEqual(dialog.selected, {0, 1})
                suggestion = SimpleNamespace(kind='entry', header_row=1,
                    mapping={'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}, warnings=[], model='fake')
                with patch('ledger_viewer.ui.deepseek_get_api_key', return_value='fake-key'), \
                     patch('ledger_viewer.ui.messagebox.askokcancel', return_value=True) as consent, \
                     patch('ledger_viewer.ui.deepseek_suggest_mapping', return_value=suggestion) as suggest, \
                     patch('ledger_viewer.ui.messagebox.showinfo'):
                    dialog._import_selected()
                    import time
                    deadline = time.monotonic() + 5
                    while dialog.winfo_exists() and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                self.assertFalse(dialog.winfo_exists())
                consent.assert_not_called()  # 密钥已设置时不再逐次弹窗确认发送预览
                self.assertEqual(suggest.call_count, 2)
                self.assertEqual(len(window.store.sources()), 2)
                self.assertEqual(window.store.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 2)
            finally:
                window._close()

    def test_deepseek_warning_pauses_import_for_review(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / '账.csv'
            source.write_text('编号,名称,增加,减少\n1002,银行存款,10,0\n', encoding='utf-8')
            app = Application()
            window = next(iter(app.windows))
            app.ai_provider = 'deepseek'
            try:
                window._use_book(base / 'book.sqlite')
                dialog = BatchImportDialog(window, [source])
                suggestion = SimpleNamespace(kind='entry', header_row=1,
                    mapping={'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3},
                    warnings=('借贷列请重点核对',), model='fake')
                with patch('ledger_viewer.ui.deepseek_get_api_key', return_value='fake-key'), \
                     patch('ledger_viewer.ui.messagebox.askokcancel', return_value=True), \
                     patch('ledger_viewer.ui.deepseek_suggest_mapping', return_value=suggestion) as suggest, \
                     patch('ledger_viewer.ui.messagebox.showwarning') as warning, \
                     patch('ledger_viewer.ui.messagebox.showinfo'):
                    dialog._import_selected()
                    import time
                    deadline = time.monotonic() + 5
                    while dialog.ai_running and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                    self.assertEqual(window.store.sources(), [])
                    warning.assert_called_once()
                    dialog._import_selected()
                suggest.assert_called_once()
                self.assertFalse(dialog.winfo_exists())
                self.assertEqual(len(window.store.sources()), 1)
            finally:
                window._close()

    def test_deepseek_provider_is_used_when_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / '账.csv'
            source.write_text('编号,名称,增加,减少\n1002,银行存款,10,0\n', encoding='utf-8')
            app = Application()
            window = next(iter(app.windows))
            try:
                app.ai_provider = 'deepseek'
                window._use_book(base / 'book.sqlite')
                dialog = BatchImportDialog(window, [source])
                suggestion = SimpleNamespace(kind='entry', header_row=1,
                    mapping={'account_code': 0, 'account_name': 1, 'debit': 2, 'credit': 3}, warnings=[], model='deepseek-flash')
                with patch('ledger_viewer.ui.deepseek_get_api_key', return_value='sk-fake'), \
                     patch('ledger_viewer.ui.deepseek_suggest_mapping', return_value=suggestion) as suggest, \
                     patch('ledger_viewer.ui.messagebox.askokcancel', return_value=True) as consent, \
                     patch('ledger_viewer.ui.messagebox.showinfo'):
                    dialog._import_selected()
                    import time
                    deadline = time.monotonic() + 5
                    while dialog.winfo_exists() and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.02)
                self.assertFalse(dialog.winfo_exists())
                consent.assert_not_called()  # 密钥已设置时不再逐次弹窗确认发送预览
                suggest.assert_called_once()
                self.assertEqual(len(window.store.sources()), 1)
                self.assertEqual(window.store.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 1)
            finally:
                window._close()


if __name__ == "__main__":
    unittest.main()
