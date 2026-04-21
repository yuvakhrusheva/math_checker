"""PDF → JPEG page images for math_checker grading pipeline."""
import base64
from pathlib import Path

import fitz  # PyMuPDF


class UnreadablePDFError(Exception):
    """Raised when a PDF cannot be opened or rendered."""


_DPI_MATRIX = fitz.Matrix(150 / 72, 150 / 72)


def pdf_to_images(pdf_path: str | Path) -> list[tuple[int, str]]:
    """
    Open a PDF and render every page to JPEG at 150 DPI.

    Returns:
        List of (page_number, base64_jpeg_string) tuples with 1-based page numbers.

    Raises:
        UnreadablePDFError: If the file cannot be opened, has 0 pages, or a page
                            fails to render.
    """
    pdf_path = str(pdf_path)
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise UnreadablePDFError(f"Cannot open PDF {pdf_path!r}: {exc}") from exc

    try:
        if doc.page_count == 0:
            raise UnreadablePDFError(f"PDF {pdf_path!r} has 0 pages")

        results: list[tuple[int, str]] = []
        for page_idx in range(doc.page_count):
            try:
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(matrix=_DPI_MATRIX)
                jpeg_bytes = pix.tobytes("jpeg")
            except Exception as exc:
                raise UnreadablePDFError(
                    f"Failed to render page {page_idx + 1} of {pdf_path!r}: {exc}"
                ) from exc

            b64 = base64.b64encode(jpeg_bytes).decode("ascii")
            results.append((page_idx + 1, b64))  # 1-based page number

        return results
    finally:
        doc.close()
