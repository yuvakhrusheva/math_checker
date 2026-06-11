"""PDF → JPEG page images for math_checker grading pipeline — v3 (stage14).

stage14:
- Учитывается page.rotation (некоторые сканеры/телефоны пишут в PDF тег
  поворота вместо того, чтобы реально перерисовать страницу).
- Снижен порог landscape c 1.2 до 1.05 — раньше слегка вытянутые
  страницы оставались «лежать».
- Добавлен параметр rotate_180 — куратор может вручную отметить, что
  скан вверх ногами, и при рендере страница повернётся на 180°.
  (Полностью автоматический детект 180° невозможен без OCR — оставлено
  на куратора.)
"""
import base64
from pathlib import Path

import fitz  # PyMuPDF


class UnreadablePDFError(Exception):
    """Raised when a PDF cannot be opened or rendered."""


_DPI = 150
_DPI_SCALE = _DPI / 72
_LANDSCAPE_RATIO = 1.05   # ширина / высота > этого → считаем landscape и крутим


def _build_render_matrix(page: fitz.Page, extra_rotation: int = 0) -> fitz.Matrix:
    """Return rendering matrix.

    - page.rotation (внутренний тег PDF) учитывается автоматически
      pymupdf при get_pixmap; мы выставляем масштаб и доп. поворот сами.
    - Если post-rotation страница окажется landscape — крутим 90° CW.
    - extra_rotation — ручной поворот (0 / 90 / 180 / 270) — например
      180 для скана вверх ногами.
    """
    rect = page.rect
    width, height = rect.width, rect.height

    # Если в PDF указан внутренний поворот 90/270 — итоговое отображение
    # уже будет «portrait», даже когда rect-ширина больше высоты. Учтём.
    pdf_rot = (getattr(page, "rotation", 0) or 0) % 360
    effective_landscape = (
        height > 0
        and (width / height) > _LANDSCAPE_RATIO
        and pdf_rot in (0, 180)
    )

    rotation = extra_rotation % 360
    if effective_landscape:
        rotation = (rotation + 90) % 360

    mat = fitz.Matrix(_DPI_SCALE, _DPI_SCALE)
    if rotation:
        mat = mat.prerotate(rotation)
    return mat


def pdf_to_images(
    pdf_path: str | Path,
    rotate_180: bool = False,
) -> list[tuple[int, str]]:
    """Render every page of a PDF to JPEG at 150 DPI.

    Args:
        pdf_path: путь к PDF.
        rotate_180: если True — каждую страницу повернуть на 180°
            (для сканов, отправленных в файл вверх ногами).
    Returns:
        List of (page_number, base64_jpeg) — 1-based.
    Raises:
        UnreadablePDFError: если PDF не открывается или страница не рендерится.
    """
    pdf_path = str(pdf_path)
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise UnreadablePDFError(f"Cannot open PDF {pdf_path!r}: {exc}") from exc

    extra = 180 if rotate_180 else 0
    try:
        if doc.page_count == 0:
            raise UnreadablePDFError(f"PDF {pdf_path!r} has 0 pages")

        results: list[tuple[int, str]] = []
        for page_idx in range(doc.page_count):
            try:
                page = doc.load_page(page_idx)
                matrix = _build_render_matrix(page, extra_rotation=extra)
                pix = page.get_pixmap(matrix=matrix)
                jpeg_bytes = pix.tobytes("jpeg")
            except Exception as exc:
                raise UnreadablePDFError(
                    f"Failed to render page {page_idx + 1} of {pdf_path!r}: {exc}"
                ) from exc

            b64 = base64.b64encode(jpeg_bytes).decode("ascii")
            results.append((page_idx + 1, b64))

        return results
    finally:
        doc.close()
