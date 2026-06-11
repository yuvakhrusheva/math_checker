"""Unit tests for src/scorer.py — TDD anchors + новые кейсы (v3)."""
from unittest.mock import patch

import pytest


def make_criteria(answer_type: str, correct_answers: list, tiers: list) -> dict:
    return {
        "task_number": 1,
        "description": "test task",
        "max_score": tiers[0]["score"],
        "answer_type": answer_type,
        "correct_answers": correct_answers,
        "tiers": tiers,
    }


NUMERIC_FULL_TIERS = [
    {"score": 4, "label": "full", "condition": "Correct answer: 42"},
    {"score": 0, "label": "zero", "condition": "Any other answer"},
]

PARTIAL_TIERS = [
    {"score": 4, "label": "full", "condition": "All 4 correct"},
    {"score": 2, "label": "partial", "condition": "2-3 correct"},
    {"score": 0, "label": "zero", "condition": "0-1 correct"},
]

ORDERED_LIST_TIERS = [
    {"score": 4, "label": "full", "condition": "All in correct order: 93, 309, 390, 930"},
    {"score": 0, "label": "zero", "condition": "Wrong order"},
]


class TestFullCredit:
    def test_full_credit(self):
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        score, notes = compute_score("42", criteria)
        assert score == 4
        assert isinstance(notes, str)

    def test_full_credit_case_insensitive(self):
        from src.scorer import compute_score
        tiers = [
            {"score": 2, "label": "full", "condition": "Correct"},
            {"score": 0, "label": "zero", "condition": "Wrong"},
        ]
        criteria = make_criteria("text", ["прямоугольник"], tiers)
        score, _ = compute_score("ПРЯМОУГОЛЬНИК", criteria)
        assert score == 2


class TestZeroCredit:
    def test_zero_credit_clearly_wrong(self):
        """Wrong NUMERIC answer → 0 (LLM-fallback не разрешает «близко»)."""
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        with patch("src.scorer._check_equivalence_via_llm", return_value=False):
            score, _ = compute_score("99", criteria)
        assert score == 0


class TestPartialCredit:
    def test_zero_credit_with_partial_tier_present(self):
        """Wrong answer goes through LLM tier classifier and hits zero."""
        from src.scorer import compute_score
        tiers = [
            {"score": 4, "label": "full", "condition": "Exact: 15"},
            {"score": 2, "label": "partial", "condition": "Partial credit"},
            {"score": 0, "label": "zero", "condition": "Wrong"},
        ]
        criteria = make_criteria("numeric", ["15"], tiers)
        with patch("src.scorer._classify_tier_via_llm", return_value="zero"):
            score, _ = compute_score("10", criteria)
        assert score == 0


class TestEmptyAnswer:
    def test_empty_answer(self):
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        score, _ = compute_score("", criteria)
        assert score == 0

    def test_none_answer(self):
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        score, _ = compute_score(None, criteria)
        assert score == 0


# --- НОВОЕ: нормализация ---

class TestNormalization:
    def test_dashes_unified(self):
        """«54 – 9 = 45» (en-dash) совпадает с эталоном «54-9=45» (ASCII)."""
        from src.scorer import compute_score
        tiers = [
            {"score": 6, "label": "full", "condition": "..."},
            {"score": 0, "label": "zero", "condition": "..."},
        ]
        criteria = make_criteria("text", ["54-9=45"], tiers)
        score, _ = compute_score("54 – 9 = 45", criteria)
        assert score == 6

    def test_spaces_collapsed(self):
        """«47 <  74» совпадает с эталоном «47 < 74»."""
        from src.scorer import compute_score
        tiers = [
            {"score": 4, "label": "full", "condition": "..."},
            {"score": 0, "label": "zero", "condition": "..."},
        ]
        criteria = make_criteria("text", ["47 < 74"], tiers)
        score, _ = compute_score("47  <   74", criteria)
        assert score == 4

    def test_trailing_dot_stripped(self):
        """«5 ман.» совпадает с «5 ман»."""
        from src.scorer import compute_score
        tiers = [
            {"score": 6, "label": "full", "condition": "..."},
            {"score": 0, "label": "zero", "condition": "..."},
        ]
        criteria = make_criteria("text", ["5 ман"], tiers)
        score, _ = compute_score("5 ман.", criteria)
        assert score == 6

    def test_ordered_list_separators(self):
        """«3-5-2» / «3 5 2» / «3, 5, 2» эквивалентны для ordered_list."""
        from src.scorer import compute_score
        criteria = make_criteria("ordered_list", ["3, 5, 2"], ORDERED_LIST_TIERS)
        for variant in ("3 5 2", "3,5,2", "3,  5,   2"):
            score, _ = compute_score(variant, criteria)
            assert score == 4, f"Failed for variant {variant!r}"


class TestEquationTail:
    """Ученик пишет «24 + 37 = 61» — должен зачестся как «61»."""

    def test_equation_tail_matches(self):
        from src.scorer import compute_score
        tiers = [
            {"score": 6, "label": "full", "condition": "Верный ответ: 61"},
            {"score": 0, "label": "zero", "condition": "Неверный"},
        ]
        criteria = make_criteria("text", ["61"], tiers)
        # _matches должен извлечь хвост после '=' и сравнить с '61'.
        score, _ = compute_score("24 + 37 = 61", criteria)
        assert score == 6


class TestEquivalenceFallback:
    """Когда нормализация не помогла — LLM-fallback решает."""

    def test_equivalent_via_llm_full(self):
        from src.scorer import compute_score
        tiers = [
            {"score": 6, "label": "full", "condition": "..."},
            {"score": 0, "label": "zero", "condition": "..."},
        ]
        criteria = make_criteria("text", ["5 манат"], tiers)
        # Эталон «5 манат», ученик «пять манат» — нормализация не сработает,
        # но LLM подтвердит эквивалентность.
        with patch("src.scorer._check_equivalence_via_llm", return_value=True):
            score, notes = compute_score("пять манат", criteria)
        assert score == 6
        assert "Semantic match" in notes

    def test_not_equivalent_via_llm_zero(self):
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        with patch("src.scorer._check_equivalence_via_llm", return_value=False):
            score, _ = compute_score("100500", criteria)
        assert score == 0

    def test_llm_exception_defaults_to_zero(self):
        from src.scorer import compute_score
        criteria = make_criteria("numeric", ["42"], NUMERIC_FULL_TIERS)
        with patch(
            "src.scorer._check_equivalence_via_llm",
            side_effect=RuntimeError("no network"),
        ):
            score, notes = compute_score("not even a number", criteria)
        assert score == 0
        assert "failed" in notes.lower() or "default" in notes.lower()


class TestVisualTask:
    """Визуальные задачи: correct_answers пуст, только full+zero."""

    def _criteria(self):
        return {
            "task_number": 2,
            "description": "Отметь рисунок с верно закрашенной 1/3 долей",
            "max_score": 4,
            "answer_type": "text",
            "correct_answers": [],
            "tiers": [
                {"score": 4, "label": "full", "condition": "Верно отмечен рисунок с 1/3"},
                {"score": 0, "label": "zero", "condition": "Неверная отметка"},
            ],
        }

    def test_visual_without_image_defaults_to_zero(self):
        from src.scorer import compute_score
        score, notes = compute_score("первый рисунок отмечен", self._criteria())
        assert score == 0
        assert "page image" in notes.lower() or "visual" in notes.lower()

    def test_visual_with_image_full(self):
        from src.scorer import compute_score
        fake_response = type("R", (), {})()
        fake_choice = type("C", (), {})()
        fake_choice.message = type("M", (), {"content": '{"label":"full","reason":"ok"}'})()
        fake_response.choices = [fake_choice]

        with patch("src.scorer.litellm.completion", return_value=fake_response):
            score, notes = compute_score(
                "первый рисунок отмечен",
                self._criteria(),
                page_image_b64="ZmFrZQ==",  # base64 of "fake"
            )
        assert score == 4
        assert "Vision" in notes

    def test_visual_with_image_zero(self):
        from src.scorer import compute_score
        fake_response = type("R", (), {})()
        fake_choice = type("C", (), {})()
        fake_choice.message = type("M", (), {"content": '{"label":"zero","reason":"wrong"}'})()
        fake_response.choices = [fake_choice]

        with patch("src.scorer.litellm.completion", return_value=fake_response):
            score, _ = compute_score(
                "первый рисунок отмечен",
                self._criteria(),
                page_image_b64="ZmFrZQ==",
            )
        assert score == 0
