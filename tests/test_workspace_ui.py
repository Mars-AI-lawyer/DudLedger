import tempfile
import unittest
from pathlib import Path

from ledger_viewer.ui import Application


class WorkspaceUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Application()
        self.app = next(iter(self.root.windows))
        self.root.set_font_size(13, persist=False)
        self.root.update()

    def tearDown(self):
        self.app._close()
        self.temp.cleanup()

    def test_first_layout_and_opening_book_reveal_workspace(self):
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertTrue(self.app.welcome.winfo_ismapped())
        self.app._use_book(Path(self.temp.name) / "演示账套.sqlite")
        self.root.update()
        self.assertFalse(self.app.welcome.winfo_ismapped())
        self.assertTrue(self.app.summary_view.winfo_ismapped())
        self.assertEqual(self.app.book_name.get(), "演示账套")
        button = self.app.nav_buttons[1]
        button.event_generate("<Enter>")
        button.event_generate("<ButtonPress-1>", x=12, y=12)
        button.event_generate("<ButtonRelease-1>", x=12, y=12)
        self.root.update()
        self.assertTrue(self.app.detail_view.winfo_ismapped())
        self.assertEqual(self.app.page_title.get(), "明细账")

    def test_compact_navigation_and_restored_sidebar_stay_in_sync(self):
        self.app._use_book(Path(self.temp.name) / "演示账套.sqlite")
        self.app.geometry("820x520")
        self.root.update()
        self.assertFalse(self.app.sidebar.winfo_ismapped())
        menu = self.app.nametowidget(self.app.page_menu.cget("menu"))
        for index, title in enumerate(self.app.page_names):
            menu.invoke(index)
            self.root.update()
            self.assertEqual(self.app.page_title.get(), title)
            self.assertTrue(self.app.nametowidget(self.app.tabs.select()).winfo_ismapped())
            self.assertTrue(self.app.nav_buttons[index].instate(["selected"]))
        self.app.geometry("1160x690")
        self.root.update()
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertEqual(self.app.page_title.get(), "AI 待核凭证")


if __name__ == "__main__":
    unittest.main()
