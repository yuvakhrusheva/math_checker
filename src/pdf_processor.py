"""PDF → JPEG page images for math_checker grading pipeline.

Изменения (Вариант А — автодетекция ориентации):
- Если страница отрендерилась в landscape (ширина заметно больше высоты),
  поворачиваем её на 90° по часовой при рендере. Покрывает случай, когда
  скан повёрнут на 90/270 — пользователь больше не видит «лежащих»
  страниц в Review Panel и Pass 1 LLM получает корректно ориентированный
  скан, что повышает точность распознавания.
- НЕ покрывает случай поворота ровно на 180° (для этого нужна отдельная
  логика, например детекция «верха» через OCR-плотность сверху/снизу).
- Порог соотношения сторон — `_LANDSCAPE_RATIO` (1.2). Если рабочие сканы
  в портрете близки к квадратным, можно поднять порог.
"""
import base64
from pathlib import Path

import fitz  # PyMuPDF


class UnreadablePDFError(Exception):
    """Raised when a PDF cannot be opened or rendered."""


_DPI = 150
_DPI_SCALE = _DPI / 72  # PyMuPDF works in 72-dpi units by default
_LANDSCAPE_RATIO = 1.2   # ширина / высота > этого → считаем landscape и крутим


def _build_render_matrix(page: fitz.Page) -> fitz.Matrix:
    """Return rendering matrix; rotates landscape pages 90° clockwise."""
    rect = page.rect
    width, height = rect.width, rect.height

    mat = fitz.Matrix(_DPI_SCALE, _DPI_SCALE)
    if height > 0 and width / height > _LANDSCAPE_RATIO:
        # landscape → rotate 90° CW so the result becomes portrait
        mat = mat.prerotate(90)
    return mat


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
                matrix = _build_render_matrix(page)
                pix = page.get_pixmap(matrix=matrix)
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
