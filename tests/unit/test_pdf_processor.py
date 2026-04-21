"""Unit tests for src/pdf_processor.py — TDD anchors, written before implementation."""
import base64
import pytest
from pathlib import Path

FIXTURE_PDF = Path("tests/fixtures/sample.pdf")
FIXTURE_MULTI_PDF = Path("tests/fixtures/sample_multi.pdf")


class TestPdfToImages:
    def test_pdf_to_images_valid(self):
        """Valid sample.pdf → list of tuples, len >= 1, each tuple (int, non-empty str)."""
        from src.pdf_processor import pdf_to_images
        result = pdf_to_images(str(FIXTURE_PDF))
        assert isinstance(result, list)
        assert len(result) >= 1
        for page_num, b64 in result:
            assert isinstance(page_num, int)
            assert isinstance(b64, str)
            assert len(b64) > 0

    def test_page_numbers_are_1based(self):
        """First page_number must be 1 (not 0)."""
        from src.pdf_processor import pdf_to_images
        result = pdf_to_images(str(FIXTURE_PDF))
        assert result[0][0] == 1

    def test_base64_is_valid_jpeg(self):
        """Decoded base64 bytes must start with JPEG magic bytes FF D8 FF."""
        from src.pdf_processor import pdf_to_images
        result = pdf_to_images(str(FIXTURE_PDF))
        _, b64 = result[0]
        raw = base64.b64decode(b64)
        assert raw[:3] == b"\xff\xd8\xff", f"Expected JPEG magic bytes, got {raw[:3].hex()}"

    def test_single_page_pdf(self):
        """Single-page PDF → list of length 1."""
        from src.pdf_processor import pdf_to_images
        result = pdf_to_images(str(FIXTURE_PDF))
        assert len(result) == 1

    def test_multi_page_pdf(self):
        """Multi-page PDF → correct count (sample_multi.pdf has 3 pages)."""
        from src.pdf_processor import pdf_to_images
        result = pdf_to_images(str(FIXTURE_MULTI_PDF))
        assert len(result) == 3

    def test_corrupted_pdf_raises_unreadable(self, tmp_path):
        """Corrupted bytes → UnreadablePDFError."""
        from src.pdf_processor import pdf_to_images, UnreadablePDFError
        bad_pdf = tmp_path / "bad.pdf"
        bad_pdf.write_bytes(b"this is not a pdf at all %%%")
        with pytest.raises(UnreadablePDFError):
            pdf_to_images(str(bad_pdf))
