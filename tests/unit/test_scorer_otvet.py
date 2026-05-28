"""Дополнительные тесты для scorer (fixes17) — извлечение ответа из multi-line."""
import pytest


def make_criteria(answer_type, correct_answers, max_score=4):
    return {
        "task_number": 1, "description": "task",
        "max_score": max_score, "answer_type": answer_type,
        "correct_answers": correct_answers,
        "tiers": [
            {"label": "full", "score": max_score, "condition": "ok"},
            {"label": "zero", "score": 0, "condition": "wrong"},
        ],
    }


class TestOtvetTail:
    def test_otvet_with_calculations(self):
        """'4*6=24, 24-10=14. Ответ: 14 наклеек' should match '14 наклеек'."""
        from src.scorer import compute_score
        crit = make_criteria("text", ["14 наклеек"], max_score=6)
        score, _ = compute_score(
            "4*6=24, 24-10=14. Ответ: 14 наклеек", crit,
        )
        assert score == 6

    def test_otvet_with_word_answer(self):
        """Long calculation chain + final 'Ответ: в среду' matches 'в среду'."""
        from src.scorer import compute_score
        crit = make_criteria("text", ["в среду"], max_score=6)
        score, _ = compute_score(
            "Пн—12, Вт—25, Ср—9, Чт—35, Пт—20. Сравним: 9<12<20<25<35. "
            "Ответ: меньше всего потратили в среду", crit,
        )
        assert score == 6


class TestSubstringMatch:
    def test_substring_long_correct_in_answer(self):
        """Эталон 'в среду' встречается в 'Меньше всего потратили в среду'."""
        from src.scorer import compute_score
        crit = make_criteria("text", ["в среду"], max_score=6)
        score, _ = compute_score(
            "Меньше всего потратили в среду", crit,
        )
        assert score == 6

    def test_substring_short_correct_not_matched(self):
        """Эталон '1' НЕ должен сматчиться в '10' (защита от ложного срабатывания)."""
        from src.scorer import compute_score
        from unittest.mock import patch
        crit = make_criteria("numeric", ["1"], max_score=4)
        # LLM-fallback вернёт False, чтобы не было «эквивалентности».
        with patch("src.scorer._check_equivalence_via_llm", return_value=False):
            score, _ = compute_score("10", crit)
        assert score == 0
