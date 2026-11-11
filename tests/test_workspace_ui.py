import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger_viewer.ui import Application


class WorkspaceUITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch("ledger_viewer.ui.Path.home", return_value=Path(self.temp.name)):
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
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertFalse(self.app.sidebar_brand.winfo_ismapped())
        self.assertTrue(all(str(button.cget("compound")) == "image" for button in self.app.sidebar_buttons))
        self.assertEqual(self.app.page_menu.winfo_class(), "TLabel")
        for index, title in enumerate(self.app.page_names):
            self.app.select_page(self.app.page_ids[index])
            self.root.update()
            self.assertEqual(self.app.page_title.get(), title)
            self.assertTrue(self.app.nametowidget(self.app.tabs.select()).winfo_ismapped())
            self.assertTrue(self.app.nav_buttons[index].instate(["selected"]))
        self.app.geometry("1160x690")
        self.root.update()
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertEqual(self.app.page_title.get(), "AI 尽调线索")

    def test_sidebar_icons_have_hover_labels_and_can_expand_to_text(self):
        button = self.app.nav_buttons[1]
        self.assertTrue(button.cget("image"))
        self.app.navigation_tooltip._show(button, lambda: "明细账")
        self.root.update()
        tooltip = self.app.navigation_tooltip.window
        self.assertTrue(tooltip.winfo_ismapped())
        self.assertEqual(tooltip.winfo_children()[0].cget("text"), "明细账")
        button.event_generate("<Leave>")
        self.root.update()
        self.assertIsNone(self.app.navigation_tooltip.window)
        self.app.sidebar_toggle.invoke()
        self.root.update()
        self.assertTrue(self.app.sidebar_brand.winfo_ismapped())
        self.assertEqual(str(button.cget("compound")), "left")
        self.app.sidebar_toggle.invoke()
        self.root.update()
        self.assertFalse(self.app.sidebar_brand.winfo_ismapped())
        self.assertEqual(str(button.cget("compound")), "image")

    def test_calculator_keeps_navigation_and_tool_buttons_available(self):
        self.app._use_book(Path(self.temp.name) / '演示账套.sqlite')
        self.app.select_page('entries')
        self.app.geometry('1160x690')
        self.root.update()
        view = self.app.detail_view
        view.xview_moveto(.05)
        self.root.update()
        first = view.xview()[0]
        self.app.calculator_button.invoke()
        self.root.update()
        self.assertTrue(self.app.detail_tools.panel.winfo_ismapped())
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertTrue(self.app.workbench_button.winfo_ismapped())
        self.assertGreaterEqual(view.winfo_width(), 520)
        self.assertEqual(view.xview(), (0.0, 1.0))
        self.app.select_page('accounts')
        self.root.update()
        self.assertIs(self.app.workbench.active_tools, self.app.balance_tools)
        self.app.select_page('entries')
        self.root.update()
        self.assertIs(self.app.workbench.active_tools, self.app.detail_tools)
        self.app.calculator_button.invoke()
        self.root.update()
        self.assertFalse(self.app.workbench.visible)
        self.assertTrue(self.app.sidebar.winfo_ismapped())
        self.assertAlmostEqual(view.xview()[0], first)


if __name__ == "__main__":
    unittest.main()
