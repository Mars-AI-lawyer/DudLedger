import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

from ledger_viewer.ui import Application, ImportDialog


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


if __name__ == "__main__":
    unittest.main()
