import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import list_marks


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class WorkflowUITest(unittest.TestCase):
    def test_parent_drilldown_inline_note_and_source_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            path = base / "entries.csv"
            path.write_text(
                "日期,会计年度,期间,凭证字,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                "2026-08-01,2026,8,记,1,6001,主营业务收入,父级,0,10\n"
                "2026-08-01,2026,8,记,1,600101,服务收入,服务费,0,100\n"
                "2026-08-01,2026,8,记,1,1002,银行存款,收款,110,0\n",
                encoding="utf-8",
            )
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            window = next(iter(app.windows))
            try:
                window._use_book(base / "case.sqlite")
                header, _, mapping = guess_header(path, "CSV", "entry")
                window.store.import_sheet(ImportSpec(path, "CSV", header, "entry", mapping))
                window.refresh_all()
                menu = window.nametowidget(window.cget("menu"))
                self.assertEqual([menu.entrycget(index, "label") for index in range(menu.index("end") + 1)], ["文件", "导入", "查看", "设置"])
                self.assertEqual([window.tabs.tab(tab, "text") for tab in window.tabs.tabs()], ["科目总览", "明细账", "资产负债表", "利润表", "关注凭证", "AI 待核凭证"])
                imports = window.nametowidget(menu.entrycget(1, "menu"))
                self.assertEqual(imports.entrycget(1, "label"), "查看导入记录")
                imports.invoke(1)
                app.update()
                self.assertEqual(window.utility_windows["导入记录"].state(), "normal")
                window.utility_windows["导入记录"].withdraw()
                review = window.nametowidget(menu.entrycget(2, "menu"))
                reminder_index = next(i for i in range(review.index("end") + 1) if review.entrycget(i, "label") == "核对提醒…")
                review.invoke(reminder_index)
                app.update()
                self.assertEqual(window.utility_windows["核对提醒"].state(), "normal")
                window.utility_windows["核对提醒"].withdraw()
                self.assertIn("tree", str(window.summary_view.cget("show")))
                self.assertIn("C:600101", window.summary_view.get_children("C:6001"))
                window.summary_view.item("C:6001", open=True)
                window.remember_expansion()
                window.refresh_all()
                self.assertTrue(window.summary_view.item("C:6001", "open"))
                window.summary_view.selection_set("C:6001")
                window.open_account(None)
                self.assertTrue(window.include_descendants)
                self.assertEqual(len(window.detail_view.get_children()), 2)
                window.detail_view.selection_set(next(iter(window.detail_view.get_children())))
                window.mark_selected_entry()
                dialog = next(iter(window.voucher_windows))
                self.assertFalse(any(type(child).__name__ == "NoteDialog" for child in descendants(dialog)))
                checkbox = next(child for child in descendants(dialog) if isinstance(child, ttk.Checkbutton) and child.cget("text") == "关注这张凭证")
                editor = next(child for child in descendants(dialog) if isinstance(child, tk.Text))
                save = next(child for child in descendants(dialog) if isinstance(child, ttk.Button) and child.cget("text") == "保存关注与注释")
                checkbox.invoke()
                editor.insert("1.0", "核对合同")
                save.invoke()
                self.assertEqual(len(list_marks(window.store)), 1)
                self.assertEqual(list_marks(window.store)[0]["note"], "核对合同")
                self.assertTrue(any("marked" in window.detail_view.item(item, "tags") for item in window.detail_view.get_children()))
                checkbox.invoke()
                save.invoke()
                self.assertEqual(list_marks(window.store), [])
                checkbox.invoke()
                editor.insert("end", "，补充流水")
                save.invoke()
                self.assertIn("补充流水", list_marks(window.store)[0]["note"])
                source_id = str(window.store.sources()[0]["id"])
                window.source_view.selection_set(source_id)
                with patch("ledger_viewer.ui.messagebox.askyesno", return_value=True):
                    window.delete_selected_source()
                self.assertTrue(path.exists())
                self.assertEqual(len(window.store.sources()), 0)
                self.assertEqual(list_marks(window.store)[0]["status"], "未匹配")
            finally:
                if window.winfo_exists():
                    window._close()


if __name__ == "__main__":
    unittest.main()
