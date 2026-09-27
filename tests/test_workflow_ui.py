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
                self.assertEqual([menu.entrycget(index, "label") for index in range(menu.index("end") + 1)], ["文件", "查看", "设置"])
                self.assertEqual([window.tabs.tab(tab, "text") for tab in window.tabs.tabs()], ["科目总览", "明细账", "资产负债表", "利润表", "关注凭证", "AI 尽调线索"])
                # 文件菜单精简为三项：导入 Excel、打开账套、退出当前账套。
                imports = window.nametowidget(menu.entrycget(0, "menu"))
                self.assertEqual([imports.entrycget(index, "label") for index in range(imports.index("end") + 1) if imports.type(index) == "command"],
                                 ["导入 Excel…", "打开账套…", "退出当前账套"])
                window.open_utility("导入记录")
                app.update()
                self.assertEqual(window.utility_windows["导入记录"].state(), "normal")
                window.utility_windows["导入记录"].withdraw()
                review = window.nametowidget(menu.entrycget(1, "menu"))
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
                self.assertTrue(window.getvar(checkbox.cget("variable")))
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
                window.tabs.select(4)
                window.refresh_marks()
                self.assertEqual(len(window.mark_view.get_children()), 1)
                window.mark_view.selection_set(next(iter(window.mark_view.get_children())))
                window.open_selected_mark_voucher()
                app.update()
                self.assertTrue(any(w.winfo_exists() and "记1" in w.title() for w in window.voucher_windows))
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


class CloseBookTest(unittest.TestCase):
    def test_close_book_returns_to_welcome_and_reopen_works(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            path = base / "entries.csv"
            path.write_text("日期,凭证字,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n"
                            "2026-08-01,记,1,1002,银行存款,收款,100,0\n", encoding="utf-8")
            try:
                app = Application()
            except tk.TclError as exc:
                self.skipTest(f"图形界面不可用：{exc}")
            window = next(iter(app.windows))
            try:
                book = base / "case.sqlite"
                window._use_book(book)
                header, _, mapping = guess_header(path, "CSV", "entry")
                window.store.import_sheet(ImportSpec(path, "CSV", header, "entry", mapping))
                window.refresh_all()
                app.update()
                self.assertEqual(len(window.detail_view.get_children()), 1)
                window.close_book()
                app.update()
                self.assertIsNone(window.store)
                self.assertEqual(window.book_name.get(), "未打开账套")
                self.assertEqual(window.detail_view.get_children(), ())
                self.assertEqual(window.summary_view.get_children(), ())
                self.assertTrue(window.welcome.winfo_ismapped())
                self.assertEqual(app.book_windows.get(book.resolve(), None), None)
                # 同一窗口可再次打开账套继续工作。
                window._use_book(book)
                app.update()
                self.assertIsNotNone(window.store)
                self.assertEqual(len(window.detail_view.get_children()), 1)
            finally:
                if window.winfo_exists():
                    window._close()


if __name__ == "__main__":
    unittest.main()
