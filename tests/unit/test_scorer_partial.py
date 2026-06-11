"""Тесты для обновлённого scorer.py — двухпроходная архитектура.

Проверяет, что:
- Точный ответ → full tier (без LLM-вызова).
- Пустой ответ → 0 (без LLM-вызова).
- Wrong answer в задаче без partial-тиров → zero tier (без LLM).
- Wrong answer в задаче с partial-тирами → LLM выбирает тир.
- Падение LLM (исключение) → безопасный фоллбек на zero.

Использует pytest-mock для подмены litellm.completion.
"""
import pytest
from unittest.mock import MagicMock


# ----------------------------- helpers -----------------------------

def make_criteria(answer_type: str, correct_answers: list, tiers: list, task_number: int = 1) -> dict:
    """Build a task criteria dict for tests."""
    return {
        "task_number": task_number,
        "description": "Test task",
        "max_score": max((t.get("score", 0) for t in tiers), default=0),
        "answer_type": answer_type,
        "correct_answers": correct_answers,
        "tiers": tiers,
    }


def make_llm_response(label: str) -> MagicMock:
    """Mock litellm.completion response that returns {'label': ...}."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = f'{{"label": "{label}"}}'
    return resp


SIMPLE_TIERS = [
    {"score": 4, "label": "full", "condition": "Correct"},
    {"score": 0, "label": "zero", "condition": "Wrong"},
]

WITH_PARTIAL = [
    {"score": 4, "label": "full", "condition": "Полностью верно"},
    {"score": 2, "label": "partial", "condition": "Верные промежуточные действия, но ошибка в итоге"},
    {"score": 0, "label": "zero", "condition": "Неверно"},
]


# ----------------------------- fast paths (без LLM) -----------------------------

class TestFastPaths:
    def test_full_match(self, mocker):
        """Точное совпадение → full, LLM не вызывается."""
        from src.scorer import compute_score
        mock_llm = mocker.patch("src.scorer.litellm.completion")
        score, notes = compute_score("42", make_criteria("text", ["42"], SIMPLE_TIERS))
        assert score == 4
        mock_llm.assert_not_called()

    def test_empty_answer(self, mocker):
        """Пустой ответ → 0, LLM не вызывается."""
        from src.scorer import compute_score
        mock_llm = mocker.patch("src.scorer.litellm.completion")
        score, notes = compute_score("", make_criteria("text", ["42"], SIMPLE_TIERS))
        assert score == 0.0
        mock_llm.assert_not_called()

    def test_none_answer(self, mocker):
        """None ответ → 0, LLM не вызывается."""
        from src.scorer import compute_score
        mock_llm = mocker.patch("src.scorer.litellm.completion")
        score, _ = compute_score(None, make_criteria("text", ["42"], SIMPLE_TIERS))
        assert score == 0.0
        mock_llm.assert_not_called()

    def test_wrong_no_partial_goes_to_zero_without_llm(self, mocker):
        """Wrong answer в задаче без partial → zero, БЕЗ вызова LLM."""
        from src.scorer import compute_score
        mock_llm = mocker.patch("src.scorer.litellm.completion")
        score, _ = compute_score("99", make_criteria("text", ["42"], SIMPLE_TIERS))
        assert score == 0
        mock_llm.assert_not_called()


# ----------------------------- partial-тиры через LLM -----------------------------

class TestPartialViaLLM:
    def test_partial_picked_by_llm(self, mocker):
        """Wrong answer в задаче с partial → LLM выбирает 'partial' → 2 балла."""
        from src.scorer import compute_score
        mocker.patch("src.scorer.litellm.completion", return_value=make_llm_response("partial"))
        score, notes = compute_score("8 + 45 = 52", make_criteria("text", ["53"], WITH_PARTIAL))
        assert score == 2

    def test_zero_picked_by_llm(self, mocker):
        """LLM выбирает 'zero' → 0 баллов."""
        from src.scorer import compute_score
        mocker.patch("src.scorer.litellm.completion", return_value=make_llm_response("zero"))
        score, _ = compute_score("100", make_criteria("text", ["53"], WITH_PARTIAL))
        assert score == 0

    def test_llm_returns_unknown_label(self, mocker):
        """LLM вернул незнакомый label → фоллбек на zero."""
        from src.scorer import compute_score
        mocker.patch("src.scorer.litellm.completion", return_value=make_llm_response("partial_extra"))
        score, notes = compute_score("foo", make_criteria("text", ["42"], WITH_PARTIAL))
        assert score == 0
        assert "unknown label" in notes

    def test_llm_returns_invalid_json(self, mocker):
        """LLM вернул мусор вместо JSON → фоллбек на zero."""
        from src.scorer import compute_score
        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = "I think it's partial!"
        mocker.patch("src.scorer.litellm.completion", return_value=bad_resp)
        score, _ = compute_score("foo", make_criteria("text", ["42"], WITH_PARTIAL))
        assert score == 0

    def test_llm_exception_falls_back_to_zero(self, mocker):
        """LLM-вызов кинул исключение → безопасно ставим zero."""
        from src.scorer import compute_score
        mocker.patch("src.scorer.litellm.completion", side_effect=ConnectionError("no internet"))
        score, notes = compute_score("foo", make_criteria("text", ["42"], WITH_PARTIAL))
        assert score == 0
        assert "LLM classifier failed" in notes


# ----------------------------- интеграция с тиерами -----------------------------

class TestTierResolution:
    def test_full_tier_score_taken_from_tier(self, mocker):
        """При полном совпадении балл берётся из full-тиры, а не max_score."""
        from src.scorer import compute_score
        mocker.patch("src.scorer.litellm.completion")
        tiers = [
            {"score": 6, "label": "full", "condition": "Correct"},
            {"score": 0, "label": "zero", "condition": "Wrong"},
        ]
        score, _ = compute_score("42", make_criteria("text", ["42"], tiers))
        assert score == 6

    def test_llm_called_only_when_partial_present(self, mocker):
        """Если в тирах нет partial — LLM не зовётся даже при wrong answer."""
        from src.scorer import compute_score
        mock_llm = mocker.patch("src.scorer.litellm.completion")
        compute_score("99", make_criteria("text", ["42"], SIMPLE_TIERS))
        mock_llm.assert_not_called()
