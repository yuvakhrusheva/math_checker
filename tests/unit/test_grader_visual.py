"""Additional tests for grader.py — visual-task behaviour (v4)."""
from unittest.mock import MagicMock, patch

import pytest


def _fake_litellm_response(content):
    response = MagicMock()
    response.choices[0].message.content = content
    return response


CRITERIA_VISUAL = {
    "grade": 3,
    "language": "ru",
    "variant": 2,
    "total_max_score": 4,
    "tasks": [
        {
            "task_number": 2,
            "description": "Choose picture with correctly shaded 1/3",
            "max_score": 4,
            "answer_type": "text",
            "correct_answers": [],
            "tiers": [
                {"label": "full", "score": 4, "condition": "Correctly shaded 1/3"},
                {"label": "zero", "score": 0, "condition": "Wrong picture"},
            ],
        }
    ],
}


CRITERIA_REGULAR = {
    "grade": 2,
    "language": "ru",
    "variant": 1,
    "total_max_score": 4,
    "tasks": [
        {
            "task_number": 1,
            "description": "Sum: 2 + 3",
            "max_score": 4,
            "answer_type": "numeric",
            "correct_answers": ["5"],
            "tiers": [
                {"label": "full", "score": 4, "condition": "Correct: 5"},
                {"label": "zero", "score": 0, "condition": "Wrong"},
            ],
        }
    ],
}


PASS1_FIRST = (
    '{"recognized_student_name":"Ivan","detected_variant":2,'
    '"tasks":[{"task_number":2,"page_number":1,'
    '"recognized_answer":"first picture marked","confidence":"high",'
    '"notes":"first picture marked"}]}'
)
PASS1_FOURTH = (
    '{"recognized_student_name":"Ivan","detected_variant":2,'
    '"tasks":[{"task_number":2,"page_number":1,'
    '"recognized_answer":"fourth picture marked","confidence":"high",'
    '"notes":"fourth picture marked"}]}'
)
PASS1_NUMERIC = (
    '{"recognized_student_name":"Ivan","detected_variant":1,'
    '"tasks":[{"task_number":1,"page_number":1,'
    '"recognized_answer":"5","confidence":"high","notes":""}]}'
)


class TestVisualTaskFlagging:
    def test_visual_task_full_is_low_confidence(self):
        from src import grader
        # Single patch on the underlying litellm module; first call = Pass 1
        # in grader, second call = vision check inside scorer.
        with patch("litellm.completion", side_effect=[
            _fake_litellm_response(PASS1_FIRST),
            _fake_litellm_response('{"label":"full","reason":"1/3 shaded"}'),
        ]):
            result = grader.grade_student(
                pages=[(1, "ZmFrZQ==")], criteria=CRITERIA_VISUAL,
            )
        task = result["tasks"][0]
        assert task["score"] == 4
        assert task["confidence"] == "low"
        assert "VISUAL" in task["grading_notes"]
        assert "MANUAL REVIEW" in task["grading_notes"]

    def test_visual_task_zero_is_low_confidence(self):
        from src import grader
        with patch("litellm.completion", side_effect=[
            _fake_litellm_response(PASS1_FOURTH),
            _fake_litellm_response('{"label":"zero","reason":"wrong fraction"}'),
        ]):
            result = grader.grade_student(
                pages=[(1, "ZmFrZQ==")], criteria=CRITERIA_VISUAL,
            )
        task = result["tasks"][0]
        assert task["score"] == 0
        assert task["confidence"] == "low"
        assert "VISUAL" in task["grading_notes"]


class TestRegularTaskUnchanged:
    def test_regular_task_keeps_high_confidence(self):
        from src import grader
        with patch("litellm.completion",
                   return_value=_fake_litellm_response(PASS1_NUMERIC)):
            result = grader.grade_student(
                pages=[(1, "ZmFrZQ==")], criteria=CRITERIA_REGULAR,
            )
        task = result["tasks"][0]
        assert task["score"] == 4
        assert task["confidence"] == "high"
        assert "VISUAL" not in task["grading_notes"]
