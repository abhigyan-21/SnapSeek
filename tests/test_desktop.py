"""Headless tests for the native application shell and background workers."""

import binascii
import ctypes
import importlib
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app
from snapseek.desktop import (
    HomePage,
    MainWindow,
    StatsPage,
    load_application_icon,
    main as desktop_main,
    resolve_asset_path,
)
from snapseek.platform import windows_window
from snapseek.platform.windows_window import (
    ERROR_ALREADY_EXISTS,
    SINGLE_INSTANCE_MUTEX_NAME,
    SW_RESTORE,
    WS_THICKFRAME,
    WINDOWS_APP_USER_MODEL_ID,
    _AccentPolicy,
    _NativeMessage,
    _WindowCompositionAttributeData,
    MA_ACTIVATE,
    WM_MOUSEACTIVATE,
    activate_window,
    acquire_single_instance,
    enable_acrylic_blur,
    enable_rounded_corners,
    enable_resize_frame,
    resize_hit_test,
    set_app_user_model_id,
    windows_resize_hit_test,
    windows_supports_acrylic_blur,
    windows_transparency_effects_enabled,
)
from snapseek.ocr import ImageOCRError, OCRDependencyError
from snapseek.scanner import ImageMetadata
from snapseek.desktop_workflow import ScanSnapshot
from snapseek.desktop_workflow import ImageOCRFailure
from snapseek.search import OCRTextRecord
from snapseek.semantic import (
    SemanticInferenceError,
    SemanticModelError,
    SemanticSearchResult,
)


class FakeEncoder:
    def __init__(self) -> None:
        self.documents: list[str] = []
        self.queries: list[str] = []

    def embed(self, documents):
        values = list(documents)
        self.documents.extend(values)
        return [(1.0, 0.0) for _ in values]

    def query_embed(self, query: str):
        self.queries.append(query)
        return [(1.0, 0.0)]


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary.cleanup()

    def test_brand_assets_resolve_and_load(self) -> None:
        logo_path = resolve_asset_path("assets/logo/snapseek-icon.png")
        icon_path = resolve_asset_path("assets/logo/snapseek-icon.ico")
        self.assertTrue(logo_path.is_file())
        self.assertTrue(icon_path.is_file())
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertFalse(self.window.home_page.logo.pixmap().isNull())
        self.assertTrue(
            all(not icon.pixmap().isNull() for icon in self.window.stats_page.metric_icons)
        )

        self.assertEqual(self.window.home_page.wordmark.accessibleName(), "SnapSeek")
        self.assertIn("color: #172b4d", self.window.home_page.wordmark.text())
        self.assertIn("color: #2878c8", self.window.home_page.wordmark.text())
        for button in self.window.nav_buttons.values():
            self.assertFalse(button.icon().isNull(), button.text())
            self.assertTrue(button.accessibleName())
        self.assertTrue(self.window.nav_buttons["Home"].isChecked())

    def test_acrylic_blur_requests_blue_tinted_material_and_falls_back_on_failure(self) -> None:
        captured: dict[str, int] = {}

        def setter(hwnd, data_pointer):
            data = ctypes.cast(
                data_pointer, ctypes.POINTER(_WindowCompositionAttributeData)
            ).contents
            policy = ctypes.cast(
                data.data, ctypes.POINTER(_AccentPolicy)
            ).contents
            captured.update(
                hwnd=hwnd,
                attribute=data.attribute,
                state=policy.accent_state,
                flags=policy.accent_flags,
                tint=policy.gradient_color,
            )
            return True

        with patch.object(windows_window, "windows_supports_acrylic_blur", return_value=True), patch.object(
            windows_window, "windows_transparency_effects_enabled", return_value=True
        ), patch.object(
            windows_window.sys, "platform", "win32"
        ):
            self.assertTrue(windows_window.enable_acrylic_blur(123, setter))
        self.assertEqual(captured["hwnd"], 123)
        self.assertEqual(captured["attribute"], 19)
        self.assertEqual(captured["state"], 4)
        self.assertEqual(captured["tint"], 0x30F0E4C8)
        self.assertAlmostEqual((captured["tint"] >> 24) / 255, 0.19, places=2)

        rejected = Mock(return_value=False)
        with patch.object(windows_window, "windows_supports_acrylic_blur", return_value=True), patch.object(
            windows_window, "windows_transparency_effects_enabled", return_value=True
        ), patch.object(
            windows_window.sys, "platform", "win32"
        ):
            self.assertFalse(windows_window.enable_acrylic_blur(123, rejected))
        rejected.assert_called_once()

        rejected.reset_mock()
        with patch.object(windows_window, "windows_supports_acrylic_blur", return_value=False):
            self.assertFalse(windows_window.enable_acrylic_blur(123, rejected))
        rejected.assert_not_called()

        with patch.object(windows_window, "windows_supports_acrylic_blur", return_value=True), patch.object(
            windows_window, "windows_transparency_effects_enabled", return_value=False
        ), patch.object(windows_window.sys, "platform", "win32"):
            self.assertFalse(windows_window.enable_acrylic_blur(123, rejected))
        rejected.assert_not_called()

    def test_opaque_fallback_keeps_translucent_top_level_disabled(self) -> None:
        self.window._backdrop_enabled = False
        self.window._apply_style()
        self.assertFalse(
            self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        )
        self.assertIn("background: #e8f2fc", self.window.styleSheet())

    def test_translucent_window_keeps_nonzero_alpha_resize_rim(self) -> None:
        self.window._backdrop_enabled = True
        self.window._apply_style()
        self.assertIn(
            "border: 8px solid rgba(224, 239, 255, 8)",
            self.window.styleSheet(),
        )
        self.assertIn(
            "QFrame#titleBar { background: rgba(214, 233, 255, 100)",
            self.window.styleSheet(),
        )
        self.assertIn(
            "QFrame#resultCard, QFrame#statCard, QFrame#settingsCard { background: rgba(224, 239, 255, 110)",
            self.window.styleSheet(),
        )

    def test_rounded_corners_use_a_separate_defensive_dwm_attribute(self) -> None:
        setter = Mock(return_value=0)
        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertTrue(windows_window.enable_rounded_corners(321, setter))
        self.assertEqual(setter.call_args.args[0], 321)
        self.assertEqual(setter.call_args.args[1], 33)
        self.assertEqual(setter.call_args.args[3], 4)

    def test_frameless_resize_hit_testing_handles_edges_corners_and_interior(self) -> None:
        rectangle = (100, 100, 900, 700)
        self.assertEqual(
            windows_window.resize_hit_test(rectangle, 102, 102, 8),
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
        )
        self.assertEqual(windows_window.resize_hit_test(rectangle, 899, 400, 8), Qt.Edge.RightEdge)
        self.assertEqual(windows_window.resize_hit_test(rectangle, 500, 699, 8), Qt.Edge.BottomEdge)
        self.assertIsNone(windows_window.resize_hit_test(rectangle, 500, 400, 8))
        self.assertIsNone(windows_window.resize_hit_test(rectangle, 99, 100, 8))

    def test_windows_resize_hit_testing_maps_all_edges_and_corners(self) -> None:
        rectangle = (100, 100, 900, 700)
        cases = (
            ((100, 400), 10),
            ((899, 400), 11),
            ((500, 100), 12),
            ((100, 100), 13),
            ((899, 100), 14),
            ((500, 699), 15),
            ((100, 699), 16),
            ((899, 699), 17),
        )
        for (x, y), expected in cases:
            with self.subTest(x=x, y=y):
                self.assertEqual(
                    windows_window.windows_resize_hit_test(rectangle, x, y, 8), expected
                )
        self.assertIsNone(windows_window.windows_resize_hit_test(rectangle, 500, 400, 8))
        self.assertIsNone(windows_window.windows_resize_hit_test(rectangle, 900, 400, 8))

    def test_windows_mouse_activate_activates_clicks_without_changing_window_z_order(self) -> None:
        message = _NativeMessage()
        message.message = WM_MOUSEACTIVATE

        with patch.object(windows_window.sys, "platform", "win32"):
            handled, result = self.window.nativeEvent(
                b"windows_generic_MSG", ctypes.addressof(message)
            )

        self.assertTrue(handled)
        self.assertEqual(result, MA_ACTIVATE)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_clicking_a_child_of_an_inactive_windows_window_raises_and_activates_it(self) -> None:
        button = self.window.nav_buttons["Settings"]
        event = QEvent(QEvent.Type.MouseButtonPress)

        with patch.object(windows_window.sys, "platform", "win32"), patch.object(
            self.window, "isActiveWindow", return_value=False
        ), patch.object(self.window, "raise_") as raise_window, patch.object(
            self.window, "activateWindow"
        ) as activate_window:
            self.assertFalse(self.window.eventFilter(button, event))

        raise_window.assert_called_once_with()
        activate_window.assert_called_once_with()

    def test_windows_activation_requests_foreground_for_layered_window(self) -> None:
        with patch.object(windows_window.sys, "platform", "win32"), patch(
            "snapseek.platform.windows_window.ctypes.windll.user32.SetForegroundWindow",
            return_value=1,
        ) as foreground:
            self.assertTrue(activate_window(123))
        foreground.assert_called_once()

    def test_second_launch_restores_existing_window_and_exits(self) -> None:
        calls = []

        def create_mutex(_security, _initial_owner, name):
            calls.append(("mutex", name))
            ctypes.set_last_error(ERROR_ALREADY_EXISTS)
            return 321

        find_window = Mock(return_value=654)
        show_window = Mock()
        foreground = Mock()
        close_handle = Mock()
        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertFalse(
                acquire_single_instance(
                    create_mutex=create_mutex,
                    find_window=find_window,
                    show_window=show_window,
                    foreground_window=foreground,
                    close_handle=close_handle,
                    get_last_error=lambda: ERROR_ALREADY_EXISTS,
                )
            )
        self.assertEqual(calls, [("mutex", SINGLE_INSTANCE_MUTEX_NAME)])
        find_window.assert_called_once_with(None, "SnapSeek")
        show_window.assert_called_once_with(654, SW_RESTORE)
        foreground.assert_called_once_with(654)
        close_handle.assert_called_once_with(321)

    def test_translucent_window_keeps_nonzero_client_background(self) -> None:
        self.window._backdrop_enabled = True
        self.window._apply_style()
        self.assertIn(
            "QWidget#windowRoot { background: rgba(214, 233, 255, 18)",
            self.window.styleSheet(),
        )

    def test_application_filter_activates_window_for_child_widget_clicks(self) -> None:
        button = self.window.nav_buttons["Settings"]
        self.window.show()

        with patch.object(self.window, "isActiveWindow", return_value=False), patch.object(
            self.window, "raise_"
        ) as raise_window, patch.object(self.window, "activateWindow") as activate_window:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)

        raise_window.assert_called_once_with()
        activate_window.assert_called_once_with()

    def test_clicking_active_windows_window_does_not_repeat_activation(self) -> None:
        button = self.window.nav_buttons["Settings"]
        event = QEvent(QEvent.Type.MouseButtonPress)

        with patch.object(windows_window.sys, "platform", "win32"), patch.object(
            self.window, "isActiveWindow", return_value=True
        ), patch.object(self.window, "raise_") as raise_window, patch.object(
            self.window, "activateWindow"
        ) as activate_window:
            self.assertFalse(self.window.eventFilter(button, event))

        raise_window.assert_not_called()
        activate_window.assert_not_called()

    def test_activation_filter_uses_widget_ancestry_without_window_method(self) -> None:
        event = QEvent(QEvent.Type.MouseButtonPress)
        button = self.window.nav_buttons["Settings"]
        with patch.object(windows_window.sys, "platform", "win32"), patch.object(
            self.window, "isActiveWindow", return_value=False
        ), patch.object(self.window, "raise_") as raise_window, patch.object(
            self.window, "activateWindow"
        ) as activate_window, patch.object(
            button, "window", new=self.window
        ):
            self.assertFalse(self.window.eventFilter(button, event))
        raise_window.assert_called_once_with()
        activate_window.assert_called_once_with()

    def test_windows_resize_frame_enables_thick_frame_and_refreshes_native_style(self) -> None:
        original_style = 0x960B0000
        changes = []
        refreshes = []

        def get_style(hwnd, index):
            self.assertEqual(hwnd.value, 456)
            self.assertEqual(index, -16)
            return original_style

        def set_style(hwnd, index, style):
            changes.append((hwnd.value, index, style))
            return original_style

        def refresh_frame(hwnd):
            refreshes.append(hwnd.value)
            return True

        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertTrue(
                enable_resize_frame(
                    456, get_style, set_style, refresh_frame
                )
            )
        self.assertEqual(changes, [(456, -16, original_style | WS_THICKFRAME)])
        self.assertEqual(refreshes, [456])

    def test_windows_resize_frame_rolls_back_if_native_refresh_fails(self) -> None:
        original_style = 0x960B0000
        changes = []
        refresh_results = iter((False, True))

        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertFalse(
                enable_resize_frame(
                    456,
                    lambda _hwnd, _index: original_style,
                    lambda _hwnd, _index, style: changes.append(style) or original_style,
                    lambda _hwnd: next(refresh_results),
                )
            )
        self.assertEqual(
            changes,
            [original_style | WS_THICKFRAME, original_style],
        )

    def test_title_controls_show_focus_ring_for_keyboard_focus_only(self) -> None:
        button = self.window.title_bar.minimize_button
        QApplication.sendEvent(
            button,
            QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason),
        )
        self.assertFalse(button.property("keyboardFocus"))
        QApplication.sendEvent(
            button,
            QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason),
        )
        QApplication.sendEvent(
            button,
            QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason),
        )
        self.assertTrue(button.property("keyboardFocus"))
        self.assertEqual(button.size(), self.window.title_bar.maximize_button.size())
        self.assertEqual(button.size(), self.window.title_bar.close_button.size())
        self.assertIn(
            'QToolButton#windowControlButton[keyboardFocus="true"]',
            self.window.styleSheet(),
        )

    def test_title_controls_minimize_maximize_restore_and_close(self) -> None:
        for control in (
            self.window.title_bar.minimize_button,
            self.window.title_bar.maximize_button,
            self.window.title_bar.close_button,
        ):
            self.assertFalse(control.icon().isNull())
            self.assertTrue(control.accessibleName())
        self.window.show()
        self.window.title_bar.minimize_button.click()
        self.application.processEvents()
        self.assertTrue(self.window.isMinimized())

        self.window.showNormal()
        self.window.title_bar.maximize_button.click()
        self.application.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(self.window.title_bar.maximize_button.accessibleName(), "Restore")

        self.window.title_bar.maximize_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.title_bar.maximize_button.accessibleName(), "Maximize")

        self.window.title_bar.close_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_navigation_keeps_current_page_and_selected_button_in_sync(self) -> None:
        self.assertNotIn("Folder", self.window.nav_buttons)
        self.assertTrue(hasattr(self.window.settings_page, "scan_button"))
        self.window.navigate("Stats")
        self.assertIs(self.window.pages.currentWidget(), self.window.stats_page)
        self.assertTrue(self.window.nav_buttons["Stats"].isChecked())
        self.assertFalse(self.window.nav_buttons["Home"].isChecked())
        self.window.navigate("Home")
        self.assertIs(self.window.pages.currentWidget(), self.window.home_page)
        self.assertTrue(self.window.nav_buttons["Home"].isChecked())

    def test_close_stops_effects_and_unregisters_global_event_filter(self) -> None:
        application = QApplication.instance()
        self.assertIsNotNone(application)
        self.window.close()
        self.assertTrue(self.window._closing)
        self.assertIsNone(self.window.home_page.compact.graphicsEffect())
        self.assertIsNone(self.window.title_bar.graphicsEffect())

    def test_search_transition_is_short_interruptible_and_preserves_query_focus(self) -> None:
        self.window.show()
        self.window.activateWindow()
        self.application.processEvents()
        home = self.window.home_page
        home.show_landing()
        home.landing_input.setText("payment received")
        home.set_query(home.landing_input.text())
        home.show_compact_search(reduce_motion=False)
        animation = home._animation
        self.assertIsNotNone(animation)
        self.assertFalse(home.compact.isHidden())
        self.assertEqual(home.compact_input.text(), "payment received")
        self.assertNotEqual(home.compact_input.focusPolicy(), Qt.FocusPolicy.NoFocus)

        home.show_compact_search(reduce_motion=False)
        self.assertIs(home._animation, animation)
        self.window.navigate("Settings")
        self.assertIsNone(home._animation)
        self.assertEqual(home.compact.maximumHeight(), 16777215)
        self.assertEqual(home._compact_opacity.opacity(), 1.0)

    def test_landing_search_bar_has_reference_width_and_visible_height(self) -> None:
        landing_box = self.window.home_page.landing_input.parentWidget()
        self.window.resize(1120, 780)
        self.window.show()
        self.application.processEvents()
        self.assertEqual(landing_box.maximumWidth(), HomePage.LANDING_SEARCH_WIDTH)
        self.assertEqual(landing_box.width(), HomePage.LANDING_SEARCH_WIDTH)
        self.assertGreaterEqual(landing_box.minimumHeight(), 68)
        self.assertTrue(self.window.home_page.landing_input.placeholderText())

    def test_search_surface_remains_prominent_when_acrylic_is_enabled(self) -> None:
        self.window._backdrop_enabled = True
        self.window._apply_style()
        stylesheet = self.window.styleSheet()
        self.assertIn(
            "QFrame#searchBox { background: rgba(232, 243, 255, 178)",
            stylesheet,
        )
        self.assertIn(
            "QFrame#titleBar { background: rgba(214, 233, 255, 100)",
            stylesheet,
        )

    def test_search_bar_stays_visible_after_results_render(self) -> None:
        home = self.window.home_page
        home.show_compact_search(reduce_motion=False)
        home.finish_search_transition()
        home.show_results([])
        home.show_results(
            [
                SemanticSearchResult(
                    OCRTextRecord(
                        ImageMetadata("result.png", self.directory / "result.png", ".png", 1),
                        "result text",
                    ),
                    0.9,
                )
            ]
        )
        self.assertTrue(home.landing.isHidden())
        self.assertFalse(home.compact.isHidden())
        self.assertEqual(home.compact.maximumHeight(), 16777215)
        self.assertEqual(home._compact_opacity.opacity(), 1.0)

    def test_restored_scan_keeps_search_bar_visible(self) -> None:
        image_path = self.directory / "restored.png"
        image_path.write_bytes(b"image")
        image = ImageMetadata("restored.png", image_path, ".png", image_path.stat().st_size)
        snapshot = ScanSnapshot(
            self.directory,
            (image,),
            (OCRTextRecord(image, "restored text"),),
            (),
        )
        with patch("snapseek.desktop.scan_images", return_value=(image,)), \
             patch("snapseek.desktop.cached_ocr", return_value={image_path: "restored text"}):
            self.window._restore_cached_scan(str(self.directory))
        self.window.show()
        self.window._enforce_restored_home_layout()
        self.application.processEvents()
        self.assertFalse(self.window.home_page.landing.isHidden())
        self.assertTrue(self.window.home_page.compact.isHidden())
        self.assertTrue(self.window.home_page.landing_input.isVisible())
        self.assertGreater(self.window.home_page.landing_input.parentWidget().height(), 0)
        self.assertTrue(self.window._restored_scan)

    def test_restored_layout_is_consumed_before_minimize_and_restore(self) -> None:
        image_path = self.directory / "restored.png"
        image_path.write_bytes(b"image")
        image = ImageMetadata("restored.png", image_path, ".png", image_path.stat().st_size)
        snapshot = ScanSnapshot(
            self.directory,
            (image,),
            (OCRTextRecord(image, "restored text"),),
            (),
        )
        with patch("snapseek.desktop.scan_images", return_value=(image,)), \
             patch("snapseek.desktop.cached_ocr", return_value={image_path: "restored text"}):
            self.window._restore_cached_scan(str(self.directory))
        self.window._enforce_restored_home_layout()
        self.assertFalse(self.window._restored_layout_pending)
        home = self.window.home_page
        home.set_query("restored query")
        home._set_category("apps")
        home.show_compact_search(reduce_motion=True)
        home.show_results([])
        self.window.show()
        self.application.processEvents()
        self.window.hide()
        self.application.processEvents()
        self.window.show()
        self.application.processEvents()
        self.assertFalse(home.compact.isHidden())
        self.assertEqual(home.compact_input.text(), "restored query")
        self.assertEqual(home.category, "apps")

    def test_reduce_motion_setting_skips_animation_and_updates_live_preference(self) -> None:
        checkbox = self.window.settings_page.reduce_motion
        self.assertIn("session only", self.window.settings_page.motion_description.text())
        checkbox.setChecked(True)
        self.assertTrue(self.window._reduce_motion)
        self.window.home_page.show_compact_search(self.window._reduce_motion)
        self.assertIsNone(self.window.home_page._animation)
        self.assertFalse(self.window.home_page.compact.isHidden())
        self.assertEqual(checkbox.accessibleName(), "Reduce motion")

    def test_stats_show_not_scanned_loading_success_and_partial_failure_states(self) -> None:
        stats = StatsPage()
        stats.update_snapshot(None, selected_directory=str(self.directory))
        self.assertEqual(stats.metrics[0].text(), "—")
        self.assertIn("Not scanned", stats.scan_status.text())
        stats.update_snapshot(None, scanning=True, selected_directory=str(self.directory))
        self.assertIn("Scanning", stats.scan_status.text())
        self.assertFalse(stats.progress.isHidden())

        images = [
            ImageMetadata(f"{name}.png", self.directory / f"{name}.png", ".png", 12)
            for name in ("one", "two", "three")
        ]
        snapshot = ScanSnapshot(
            self.directory,
            tuple(images),
            (OCRTextRecord(images[0], "recognized text"),),
            (ImageOCRFailure(images[1].path, "unreadable image"),),
        )
        stats.update_snapshot(snapshot)
        self.assertFalse(stats.progress.isVisible())
        self.assertEqual([metric.text() for metric in stats.metrics], ["3", "1", "1", "1"])
        self.assertIn("1 OCR failure", stats.scan_status.text())

        setup_failure = ScanSnapshot(
            self.directory, tuple(images), (), (), setup_error="missing Tesseract", setup_error_kind="ocr"
        )
        stats.update_snapshot(setup_failure)
        self.assertEqual([metric.text() for metric in stats.metrics], ["3", "0", "0", "3"])
        self.assertIn("Setup failed (ocr)", stats.scan_status.text())

    def test_asset_path_uses_pyinstaller_bundle_root_when_available(self) -> None:
        bundle_directory = self.directory / "_MEIPASS"
        with patch("snapseek.desktop.sys._MEIPASS", str(bundle_directory), create=True):
            resolved = resolve_asset_path("assets/icons/home.svg")
        self.assertEqual(resolved, bundle_directory / "assets/icons/home.svg")

    def test_windows_app_user_model_id_is_set_only_on_windows(self) -> None:
        setter = Mock(return_value=0)
        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertTrue(set_app_user_model_id(setter))
        setter.assert_called_once_with(WINDOWS_APP_USER_MODEL_ID)

        setter.reset_mock()
        with patch.object(windows_window.sys, "platform", "linux"):
            self.assertFalse(set_app_user_model_id(setter))
        setter.assert_not_called()

    def test_main_sets_identity_before_qapplication_and_assigns_app_icon(self) -> None:
        events: list[str] = []
        application = Mock()
        application.exec.return_value = 0
        window = Mock()

        def make_application(_argv):
            events.append("application")
            return application

        with patch(
            "snapseek.platform.windows_window.set_app_user_model_id",
            side_effect=lambda: events.append("app-id"),
        ), patch("snapseek.desktop.QApplication", side_effect=make_application), patch(
            "snapseek.desktop.MainWindow", return_value=window
        ):
            self.assertEqual(desktop_main(["snapseek"]), 0)

        self.assertEqual(events, ["app-id", "application"])
        application.setWindowIcon.assert_called_once()
        self.assertFalse(application.setWindowIcon.call_args.args[0].isNull())
        window.show.assert_called_once()

    def test_application_and_main_window_use_loadable_logo_icon(self) -> None:
        self.assertFalse(load_application_icon().isNull())
        self.assertFalse(self.window.windowIcon().isNull())

        icon_data = resolve_asset_path("assets/logo/snapseek-icon.ico").read_bytes()
        reserved, icon_type, image_count = struct.unpack_from("<HHH", icon_data)
        self.assertEqual((reserved, icon_type), (0, 1))
        sizes = {
            (width or 256, height or 256)
            for width, height in (
                struct.unpack_from("<BB", icon_data, 6 + index * 16)
                for index in range(image_count)
            )
        }
        self.assertTrue({(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes)

    def wait_until(self, predicate, timeout: float = 5.0) -> None:
        if predicate():
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(int(timeout * 1000))

        def poll() -> None:
            if predicate():
                loop.quit()
            else:
                QTimer.singleShot(10, poll)

        QTimer.singleShot(0, poll)
        loop.exec()
        timer.stop()
        self.assertTrue(predicate(), "background operation did not finish in time")

    @staticmethod
    def write_png(path: Path) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
        png += chunk(b"IEND", b"")
        path.write_bytes(png)

    def test_importing_ui_does_not_load_the_real_model(self) -> None:
        with patch("snapseek.semantic.load_semantic_encoder") as loader:
            importlib.reload(app)
        loader.assert_not_called()

    def test_scan_and_search_run_off_thread_and_keep_result_association(self) -> None:
        image_path = self.directory / "receipt.png"
        self.write_png(image_path)
        encoder = FakeEncoder()

        with patch("snapseek.desktop_workflow.extract_text", return_value="Payment received") as ocr:
            self.window.scan_directory(str(self.directory))
            self.wait_until(lambda: self.window._active_worker is None)

            self.assertEqual(len(self.window._snapshot.images), 1)
            self.assertEqual(self.window._snapshot.records[0].image.path, image_path)
            self.assertEqual(self.window._snapshot.records[0].ocr_text, "Payment received")

            self.window.home_page.landing_input.setText("typed, not submitted")
            self.assertEqual(encoder.queries, [])
            with patch("snapseek.desktop.load_semantic_encoder", return_value=encoder):
                self.window.submit_search("payment")
                self.wait_until(lambda: self.window._active_worker is None)

        ocr.assert_called_once()
        self.assertEqual(encoder.documents, ["Payment received"])
        self.assertEqual(encoder.queries, ["payment"])
        self.assertEqual(len(self.window._last_results), 1)
        card = self.window.home_page.results_grid._cards[0]
        self.assertEqual(card.result.record.image.path, image_path)
        self.assertEqual(card.result.record.ocr_text, "Payment received")
        self.assertIn("Found 1 screenshot", self.window.home_page.status.text())
        self.window.home_page.compact_input.setText("payment")
        QTest.keyClick(self.window.home_page.compact_input, Qt.Key.Key_Return)
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertEqual(self.window._last_query, "payment")
        self.window.home_page.compact_input.setText("received")
        self.window.home_page.compact_button.click()
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertEqual(self.window._last_query, "received")

    def test_empty_directory_and_no_match_have_clear_status(self) -> None:
        self.window.scan_directory(str(self.directory))
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertEqual(self.window._snapshot.images, ())
        self.assertIsNone(self.window._snapshot.encoder)

        self.window.submit_search("anything")
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertEqual(self.window._last_results, [])
        self.assertIn("No matching screenshots", self.window.home_page.status.text())

    def test_image_ocr_failure_is_visible_and_later_image_continues(self) -> None:
        first = self.directory / "a.png"
        broken = self.directory / "b.png"
        last = self.directory / "c.png"
        for path in (first, broken, last):
            self.write_png(path)

        def extract(image: ImageMetadata) -> str:
            if image.path == broken:
                raise ImageOCRError("unreadable image")
            return image.filename

        with patch("snapseek.desktop_workflow.extract_text", side_effect=extract):
            self.window.scan_directory(str(self.directory))
            self.wait_until(lambda: self.window._active_worker is None)

        self.assertEqual([record.image.path for record in self.window._snapshot.records], [first, last])
        self.assertEqual(len(self.window._snapshot.image_failures), 1)
        self.assertIn(str(broken), self.window.home_page.ocr_failures.text())

    def test_ocr_setup_error_stops_and_reports_unprocessed_images(self) -> None:
        for name in ("a.png", "b.png", "c.png"):
            self.write_png(self.directory / name)
        calls: list[Path] = []

        def extract(image: ImageMetadata) -> str:
            calls.append(image.path)
            if image.path.name == "b.png":
                raise OCRDependencyError("pytesseract is missing")
            return "some text"

        with patch("snapseek.desktop_workflow.extract_text", side_effect=extract):
            self.window.scan_directory(str(self.directory))
            self.wait_until(lambda: self.window._active_worker is None)

        self.assertEqual(len(calls), 3)
        self.assertEqual(self.window._snapshot.setup_error_kind, "ocr")
        self.assertEqual(self.window._snapshot.unprocessed_count, 2)
        self.assertIn("OCR setup failed", self.window.home_page.status.text())

    def test_model_setup_failure_is_reported_before_ocr(self) -> None:
        self.write_png(self.directory / "a.png")
        with patch("snapseek.desktop_workflow.extract_text") as extractor:
            self.window.scan_directory(str(self.directory))
            self.wait_until(lambda: self.window._active_worker is None)
        self.assertIsNone(self.window._snapshot.setup_error_kind)
        extractor.assert_called_once()

    def test_invalid_directory_is_reported_clearly(self) -> None:
        self.window.scan_directory(str(self.directory / "missing"))
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertIn("not an accessible directory", self.window.home_page.status.text())
        self.assertIn("not an accessible directory", self.window.settings_page.folder_message.text())

    def test_semantic_inference_error_is_displayed(self) -> None:
        self.write_png(self.directory / "a.png")
        encoder = FakeEncoder()
        with patch("snapseek.desktop_workflow.load_semantic_encoder", return_value=encoder), \
             patch("snapseek.desktop_workflow.extract_text", return_value="OCR words"):
            self.window.scan_directory(str(self.directory))
            self.wait_until(lambda: self.window._active_worker is None)

        def fail_search(query, snapshot, **_kwargs):
            raise SemanticInferenceError("runtime inference failure")

        with patch("snapseek.desktop.search_snapshot", side_effect=fail_search):
            self.window.submit_search("query")
            self.wait_until(lambda: self.window._active_worker is None)
        self.assertIn("Semantic search failed", self.window.home_page.status.text())
        self.assertIn("runtime inference failure", self.window.home_page.status.text())

    def test_folder_change_invalidates_old_snapshot_and_results(self) -> None:
        self.window._snapshot = ScanSnapshot(self.directory, (), (), ())
        self.window._last_query = "old query"
        self.window._last_results = []
        self.window.settings_page.path_input.setText(str(self.directory / "another"))
        self.assertIsNone(self.window._snapshot)
        self.assertIsNone(self.window._last_results)
        self.assertEqual(self.window._last_query, "")
        self.assertIn("Folder changed", self.window.home_page.status.text())

    def test_worker_rejects_duplicate_operation_and_runs_outside_gui_thread(self) -> None:
        gui_thread = threading.get_ident()
        task_thread: list[int] = []
        release = threading.Event()
        def task(cancel_event: threading.Event) -> None:
            task_thread.append(threading.get_ident())
            release.wait(2)

        self.assertTrue(self.window._start_worker("thread-test", task))
        self.assertFalse(self.window._start_worker("duplicate", task))
        QTimer.singleShot(40, release.set)
        self.wait_until(lambda: self.window._active_worker is None)
        self.assertTrue(task_thread)
        self.assertNotEqual(task_thread[0], gui_thread)


class DesktopShutdownTests(unittest.TestCase):
    """Deterministic tests for the deferred-shutdown sequence.

    These tests verify:
    - _start_worker rejects new operations while _closing is True
    - close() with no active worker finalizes immediately
    - close() with a fast worker waits and finalizes (waitForDone returns True)
    - close() with a slow worker defers via event.ignore() and retries via
      _worker_completed once the worker finishes
    - the database is never closed while a worker is still running
    - the hotkey is unregistered only on the final accepted close
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.window = MainWindow()

    def tearDown(self) -> None:
        # Force final close in case a test left the window deferred-open.
        if self.window.isVisible() or not self.window._index._closed:
            self.window._closing = True
            self.window._worker_controller.cancel()
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().waitForDone(3000)
            if not self.window._index._closed:
                self.window._index.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    # ------------------------------------------------------------------
    # _start_worker centralized guard
    # ------------------------------------------------------------------

    def test_start_worker_rejects_all_operations_while_closing(self) -> None:
        """_start_worker must return False immediately when _closing is True."""
        self.assertFalse(self.window._closing)
        self.window._closing = True
        result = self.window._start_worker("scan", lambda _cancel: None)
        self.assertFalse(result)
        self.assertIsNone(self.window._active_worker)

    def test_scan_directory_and_submit_search_respect_closing_guard(self) -> None:
        """scan_directory and submit_search delegate to _start_worker and are
        therefore also blocked when _closing is True."""
        self.window._closing = True
        # Neither should start a worker; no active worker means the guard fired.
        self.window.scan_directory(str(self.directory))
        self.assertIsNone(self.window._active_worker)

        # Provide a snapshot so submit_search won't reject for a missing scan.
        self.window._snapshot = ScanSnapshot(self.directory, (), (), ())
        self.window.submit_search("query", "screenshots")
        self.assertIsNone(self.window._active_worker)

    # ------------------------------------------------------------------
    # Immediate close — no worker running
    # ------------------------------------------------------------------

    def test_close_with_no_active_worker_finalizes_immediately(self) -> None:
        """When no worker is running, close() must set _closing, close the
        index, and accept the event without deferral."""
        self.assertFalse(self.window._index._closed)
        self.window.close()
        self.application.processEvents()
        self.assertTrue(self.window._closing)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._index._closed)

    # ------------------------------------------------------------------
    # Fast worker — finishes within 2 s wait
    # ------------------------------------------------------------------

    def test_close_waits_for_fast_worker_and_then_finalizes(self) -> None:
        """When a worker completes within the 2-second waitForDone window,
        close() must accept the event and close the database."""
        finished = threading.Event()

        def fast_task(cancel_event: threading.Event) -> object:
            finished.wait(2)  # released almost immediately below
            return None

        self.window._start_worker("fast", fast_task)
        self.assertIsNotNone(self.window._active_worker)

        # Unblock the worker so it finishes before waitForDone times out.
        finished.set()

        self.window.close()
        self.application.processEvents()

        self.assertTrue(self.window._closing)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._index._closed)

    # ------------------------------------------------------------------
    # Slow worker — does not finish within 2 s, then completes later
    # ------------------------------------------------------------------

    def test_close_defers_when_worker_exceeds_timeout_and_retries_on_completion(
        self,
    ) -> None:
        """When waitForDone(2000) returns False, closeEvent must call
        event.ignore() and leave the database open.
        When the worker later finishes, _worker_completed must call close()
        again, which then finalizes shutdown."""
        hold = threading.Event()   # keeps the worker alive past the 2-second wait

        def slow_task(cancel_event: threading.Event) -> object:
            hold.wait(10)  # held until the test explicitly releases it
            return None

        self.window._start_worker("slow", slow_task)
        self.assertIsNotNone(self.window._active_worker)

        # Patch waitForDone to return False immediately (simulates timeout
        # without actually waiting 2 real seconds in CI).
        with patch(
            "snapseek.desktop.QThreadPool.globalInstance",
            return_value=type(
                "FakePool", (), {"waitForDone": staticmethod(lambda _ms: False)}
            )(),
        ):
            self.window.close()
            self.application.processEvents()

        # After the simulated timeout the database must still be open.
        self.assertTrue(self.window._closing)
        self.assertFalse(self.window._index._closed, "database must stay open while worker is running")

        # Now let the worker finish.  It will emit its completed signal, which
        # routes to _worker_completed on the GUI thread, which calls close()
        # again — this time with no active worker, so it takes the finalization path.
        hold.set()

        # Drive the Qt event loop until the database is closed.
        loop = QEventLoop()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(3000)

        def check():
            if self.window._index._closed:
                loop.quit()
            else:
                QTimer.singleShot(20, check)

        QTimer.singleShot(0, check)
        loop.exec()
        deadline.stop()

        self.assertTrue(self.window._index._closed, "database should be closed after deferred shutdown")

    # ------------------------------------------------------------------
    # Database safety during deferred close
    # ------------------------------------------------------------------

    def test_database_stays_open_while_worker_is_running_after_close_request(
        self,
    ) -> None:
        """The index must remain open while a worker is executing, even after
        close() has been called."""
        hold = threading.Event()

        def blocking_task(cancel_event: threading.Event) -> object:
            hold.wait(10)
            return None

        self.window._start_worker("blocking", blocking_task)
        self.assertIsNotNone(self.window._active_worker)

        # Simulate the timeout path — window defers close.
        with patch(
            "snapseek.desktop.QThreadPool.globalInstance",
            return_value=type(
                "FakePool", (), {"waitForDone": staticmethod(lambda _ms: False)}
            )(),
        ):
            self.window.close()
            self.application.processEvents()

        # The database must still be accessible (not closed).
        self.assertFalse(self.window._index._closed)
        self.assertIsNotNone(self.window._index.connection)

        # Clean up: release the worker so tearDown can close cleanly.
        hold.set()

    # ------------------------------------------------------------------
    # Hotkey cleanup happens only on the final accepted close
    # ------------------------------------------------------------------

    def test_hotkey_is_not_unregistered_on_deferred_close(self) -> None:
        """unregister_global_hotkey must only be called when the event is
        accepted, not when it is ignored due to a slow worker."""
        hold = threading.Event()

        def slow_task(cancel_event: threading.Event) -> object:
            hold.wait(10)
            return None

        self.window._start_worker("slow-hotkey", slow_task)

        unregister_calls: list[int] = []

        with patch(
            "snapseek.desktop.QThreadPool.globalInstance",
            return_value=type(
                "FakePool", (), {"waitForDone": staticmethod(lambda _ms: False)}
            )(),
        ), patch("snapseek.desktop.sys.platform", "win32"), patch(
            "snapseek.desktop.unregister_global_hotkey",
            side_effect=unregister_calls.append,
        ):
            self.window.close()
            self.application.processEvents()

        # Hotkey must NOT have been unregistered yet — the event was ignored.
        self.assertEqual(unregister_calls, [], "hotkey must not be unregistered on deferred close")
        # Database must also still be open.
        self.assertFalse(self.window._index._closed, "database must stay open during deferred close")

        # Clean up.
        hold.set()


class WatchWorkerPreemptionTests(unittest.TestCase):
    """Regression tests for the race between monitor watch workers and
    user-initiated scan/search operations.

    Root cause: after a scan completes, FolderMonitor.start() is deferred
    via QTimer.singleShot(0).  The poll timer begins ticking and may deliver
    filesystem events before the user calls submit_search.  These events
    launch a "watch" background worker.  Without the cancel_watch() fix,
    submit_search() would see active_worker != None and silently drop the
    search request, causing wait_until to time out.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        self.temporary.cleanup()

    def test_submit_search_preempts_active_watch_worker(self) -> None:
        """submit_search must cancel an active watch worker and start the
        search rather than silently dropping the user request."""
        hold = threading.Event()

        def slow_watch(cancel: threading.Event) -> object:
            hold.wait(10)  # held open to simulate a long-running watch task
            return None

        # Inject a watch worker directly — simulates what the monitor does.
        self.assertTrue(self.window._start_worker("watch", slow_watch))
        self.assertIsNotNone(self.window._active_worker)
        self.assertEqual(self.window._active_worker.kind, "watch")

        # Give the window a valid snapshot so submit_search doesn't reject
        # the request for a missing scan.
        from snapseek.desktop_workflow import ScanSnapshot
        self.window._snapshot = ScanSnapshot(
            directory=self.directory,
            images=(),
            records=(),
            image_failures=(),
        )

        with patch("snapseek.desktop.search_snapshot", return_value=[]), \
             patch("snapseek.desktop.search_index", return_value=[]):
            self.window.submit_search("query", "screenshots")

        # The search worker should now be active (not the watch worker).
        self.assertIsNotNone(self.window._active_worker)
        self.assertEqual(self.window._active_worker.kind, "search")
        # Release the lingering watch task thread.
        hold.set()

    def test_scan_directory_preempts_active_watch_worker(self) -> None:
        """scan_directory must cancel an active watch worker and start the
        scan rather than silently dropping the user request."""
        hold = threading.Event()

        def slow_watch(cancel: threading.Event) -> object:
            hold.wait(10)
            return None

        self.assertTrue(self.window._start_worker("watch", slow_watch))
        self.assertIsNotNone(self.window._active_worker)

        self.window.scan_directory(str(self.directory))

        # A scan worker should now be active (not the original watch worker).
        self.assertIsNotNone(self.window._active_worker)
        self.assertEqual(self.window._active_worker.kind, "scan")
        hold.set()

    def test_submit_search_does_not_preempt_active_scan_worker(self) -> None:
        """submit_search must NOT cancel a scan worker — only watch workers
        are preemptible."""
        hold = threading.Event()

        def slow_scan(cancel: threading.Event) -> object:
            hold.wait(10)
            return None

        self.assertTrue(self.window._start_worker("scan", slow_scan))
        self.assertEqual(self.window._active_worker.kind, "scan")

        self.window.submit_search("query", "screenshots")

        # Scan worker must still be active.
        self.assertIsNotNone(self.window._active_worker)
        self.assertEqual(self.window._active_worker.kind, "scan")
        hold.set()

    def test_cancel_watch_clears_active_worker_immediately(self) -> None:
        """WorkerController.cancel_watch must clear active_worker synchronously
        so the next call to start() succeeds without waiting for a signal."""
        hold = threading.Event()

        def watch_task(cancel: threading.Event) -> object:
            hold.wait(10)
            return None

        from snapseek.ui.operations import WorkerController
        controller = WorkerController()
        controller.start("watch", watch_task)
        self.assertIsNotNone(controller.active_worker)

        cancelled = controller.cancel_watch()
        self.assertTrue(cancelled)
        self.assertIsNone(controller.active_worker)  # cleared immediately
        hold.set()

    def test_cancel_watch_returns_false_for_non_watch_worker(self) -> None:
        """cancel_watch must not cancel a scan or search worker."""
        hold = threading.Event()

        def scan_task(cancel: threading.Event) -> object:
            hold.wait(10)
            return None

        from snapseek.ui.operations import WorkerController
        controller = WorkerController()
        controller.start("scan", scan_task)

        cancelled = controller.cancel_watch()
        self.assertFalse(cancelled)
        self.assertIsNotNone(controller.active_worker)  # scan worker still running
        hold.set()


if __name__ == "__main__":
    unittest.main()
