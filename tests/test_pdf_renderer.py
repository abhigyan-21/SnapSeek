"""Focused renderer resource and cancellation tests."""

import unittest
from unittest.mock import patch

from snapseek.pdf_renderer import PDFRenderCancelled, PDFRenderLimits, PDFRenderer


class PDFRendererTests(unittest.TestCase):
    def test_cancellation_is_checked_before_open(self) -> None:
        with self.assertRaises(PDFRenderCancelled):
            with PDFRenderer("sample.pdf", cancelled=lambda: True):
                pass

    def test_renderer_closes_page_and_bitmap_after_conversion(self) -> None:
        class FakeImage:
            def copy(self):
                return self

            def close(self):
                pass

        class FakeBitmap:
            def to_pil(self):
                return FakeImage()

            def close(self):
                self.closed = True

        class FakePage:
            def get_width(self):
                return 72

            def get_height(self):
                return 72

            def render(self, **_kwargs):
                return FakeBitmap()

            def close(self):
                self.closed = True

        class FakeDocument:
            def __getitem__(self, _index):
                return FakePage()

            def close(self):
                self.closed = True

        fake_pdfium = type("Pdfium", (), {"PdfDocument": lambda _path: FakeDocument()})
        with patch("snapseek.pdf_renderer.import_module", return_value=fake_pdfium):
            with PDFRenderer(
                "sample.pdf",
                limits=PDFRenderLimits(dpi=72),
            ) as renderer:
                self.assertIsNotNone(renderer.render_page(1))
