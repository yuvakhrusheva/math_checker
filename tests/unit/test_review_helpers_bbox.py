"""Тесты для crop_image_by_bbox из pages/review_panel.py."""
import io
import json

import pytest
from PIL import Image


def _make_jpeg(width: int, height: int, color: tuple = (200, 150, 100)) -> bytes:
    """Build a solid-color JPEG for tests."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _size(img_bytes: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(img_bytes)).size


class TestCropImageByBbox:
    def test_no_bbox_returns_original(self):
        """bbox=None → возвращаем оригинал без изменений."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        result = crop_image_by_bbox(original, None)
        assert result is original

    def test_empty_bbox_returns_original(self):
        """Пустая строка → оригинал."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        result = crop_image_by_bbox(original, "")
        assert result is original

    def test_invalid_json_returns_original(self):
        """Невалидный JSON → оригинал."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        result = crop_image_by_bbox(original, "not-json")
        assert result is original

    def test_bbox_missing_fields_returns_original(self):
        """Bbox без всех полей → оригинал."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        result = crop_image_by_bbox(original, json.dumps({"x1": 0.1, "y1": 0.2}))
        assert result is original

    def test_bbox_inverted_returns_original(self):
        """x2 <= x1 → оригинал (некорректный bbox)."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        bbox = json.dumps({"x1": 0.5, "y1": 0.2, "x2": 0.2, "y2": 0.4})
        result = crop_image_by_bbox(original, bbox)
        assert result is original

    def test_valid_bbox_crops_correctly(self):
        """Корректный bbox 0..1 → возвращает обрезанное изображение нужного размера."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 800)
        # bbox от 25% до 75% по обеим осям → размер 200x400
        bbox = json.dumps({"x1": 0.25, "y1": 0.25, "x2": 0.75, "y2": 0.75})
        result = crop_image_by_bbox(original, bbox)
        assert result is not original
        w, h = _size(result)
        assert 195 <= w <= 205  # допускаем погрешность в 1 пиксель
        assert 395 <= h <= 405

    def test_bbox_clamped_to_image_bounds(self):
        """Координаты за границами [0,1] обрезаются: x2=1.5 → 1.0."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        bbox = json.dumps({"x1": -0.1, "y1": 0.0, "x2": 1.5, "y2": 0.5})
        result = crop_image_by_bbox(original, bbox)
        # После clipping: x1=0, x2=1, y1=0, y2=0.5 → размер 400x300
        w, h = _size(result)
        assert w == 400
        assert 295 <= h <= 305

    def test_tiny_bbox_returns_original(self):
        """Очень мелкий bbox (<10px) → оригинал, чтобы не показывать пустой кусок."""
        from pages.review_panel import crop_image_by_bbox
        original = _make_jpeg(400, 600)
        bbox = json.dumps({"x1": 0.5, "y1": 0.5, "x2": 0.501, "y2": 0.501})
        result = crop_image_by_bbox(original, bbox)
        assert result is original
