import ctypes
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt

from snapseek.platform import windows_window


class WindowsWindowTests(unittest.TestCase):
    def test_resize_hit_test_maps_corner_and_outside_points(self) -> None:
        self.assertEqual(
            windows_window.windows_resize_hit_test((0, 0, 100, 100), 2, 2, 8),
            windows_window.HT_TOPLEFT,
        )
        self.assertIsNone(
            windows_window.windows_resize_hit_test((0, 0, 100, 100), 50, 50, 8)
        )
        self.assertEqual(
            windows_window.resize_hit_test((0, 0, 100, 100), 50, 2, 8),
            Qt.Edge.TopEdge,
        )

    def test_acrylic_blur_populates_native_composition_payload(self) -> None:
        captured = {}

        def setter(hwnd, pointer):
            data = ctypes.cast(
                pointer, ctypes.POINTER(windows_window._WindowCompositionAttributeData)
            ).contents
            policy = ctypes.cast(
                data.data, ctypes.POINTER(windows_window._AccentPolicy)
            ).contents
            captured.update(hwnd=hwnd, attribute=data.attribute, state=policy.accent_state)
            return True

        with patch.object(windows_window, "windows_supports_acrylic_blur", return_value=True), \
            patch.object(
                windows_window, "windows_transparency_effects_enabled", return_value=True
            ):
            self.assertTrue(windows_window.enable_acrylic_blur(123, setter))

        self.assertEqual(captured, {"hwnd": 123, "attribute": 19, "state": 4})

    def test_resize_frame_adds_thick_frame_and_refreshes(self) -> None:
        get_style = Mock(return_value=0)
        set_style = Mock(return_value=0)
        refresh = Mock(return_value=True)

        with patch.object(windows_window.sys, "platform", "win32"):
            self.assertTrue(
                windows_window.enable_resize_frame(
                    123, get_style, set_style, refresh
                )
            )

        get_style.assert_called_once()
        set_style.assert_called_once()
        handle, index, style = set_style.call_args.args
        self.assertEqual(handle.value, 123)
        self.assertEqual(index, windows_window.GWL_STYLE)
        self.assertEqual(style, windows_window.WS_THICKFRAME)
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0].value, 123)


if __name__ == "__main__":
    unittest.main()
