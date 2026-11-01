import unittest

from PySide6.QtWidgets import QApplication, QWidget

from snapseek.ui.navigation import WindowTitleBar
from snapseek.ui.pages import FolderPage, HomePage, SettingsPage, StatsPage


APPLICATION = QApplication.instance() or QApplication([])


class UIPageTests(unittest.TestCase):
    def test_pages_construct_with_expected_signals_and_controls(self) -> None:
        home = HomePage()
        folder = FolderPage()
        stats = StatsPage()
        settings = SettingsPage()

        self.assertEqual(home.category, "all")
        self.assertEqual(len(home.example_buttons), 3)
        self.assertTrue(folder.scan_button.isEnabled())
        self.assertEqual(len(stats.metrics), 4)
        self.assertTrue(settings.reduce_motion.isCheckable())

        home.deleteLater()
        folder.deleteLater()
        stats.deleteLater()
        settings.deleteLater()

    def test_category_toggle_emits_selected_category(self) -> None:
        home = HomePage()
        selected: list[str] = []
        home.category_changed.connect(selected.append)

        home._set_category("files")

        self.assertEqual(home.category, "files")
        self.assertEqual(selected, ["files"])
        home.deleteLater()

    def test_window_title_bar_owns_navigation_and_window_controls(self) -> None:
        class FakeWindow(QWidget):
            def showMinimized(self): pass
            def toggle_maximized(self): pass
            def close(self): pass

        title_bar = WindowTitleBar(FakeWindow())
        self.assertEqual(title_bar.navigation.count(), 0)
        self.assertEqual(title_bar.minimize_button.accessibleName(), "Minimize")
        self.assertEqual(title_bar.maximize_button.accessibleName(), "Maximize")
        title_bar.deleteLater()


if __name__ == "__main__":
    unittest.main()
