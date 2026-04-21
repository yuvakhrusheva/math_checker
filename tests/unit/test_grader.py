"""Unit tests for src/grader.py — TDD anchors, no real LLM calls."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

FIXTURE_PATH = Path("tests/fixtures/llm_responses/sample_response.json")


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def make_minimal_criteria() -> dict:
    return {
        "grade": 3,
        "language": "ru",
        "variant": 1,
        "total_max_score": 100,
        "tasks": [
            {
                "task_number": 1,
                "description": "Расставьте числа по возрастанию",
                "max_score": 4,
                "answer_type": "ordered_list",
                "correct_answers": ["93, 309, 390, 930"],
                "tiers": [
                    {"score": 4, "label": "full", "condition": "Все верно"},
                    {"score": 0, "label": "zero", "condition": "Ошибка"},
                ],
            }
        ],
    }


def make_pages() -> list:
    """Minimal pages list for testing (fake base64)."""
    return [(1, "aGVsbG8="), (2, "d29ybGQ=")]


class TestBuildPrompt:
    def test_build_prompt_structure(self):
        """build_prompt returns a string containing task descriptions."""
        from src.grader import build_prompt
        criteria = make_minimal_criteria()
        pages = make_pages()
        prompt = build_prompt(criteria, pages)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "1" in prompt  # task number present
        assert "возрастанию" in prompt or "task" in prompt.lower() or "задан" in prompt.lower() or "расставьте" in prompt.lower()


class TestParseValidResponse:
    def test_parse_valid_response(self):
        """Fixture JSON parsed correctly → list of task result dicts with grading_notes."""
        from src.grader import _parse_llm_response
        fixture = load_fixture()
        raw_json = json.dumps(fixture)
        result = _parse_llm_response(raw_json)
        assert result["recognized_student_name"] == "Алиев Айдын"
        assert result["detected_variant"] == 1
        tasks = result["tasks"]
        assert len(tasks) == 16
        # Verify notes → grading_notes mapping
        assert "grading_notes" in tasks[0]
        assert "notes" not in tasks[0]

    def test_task_result_fields_present(self):
        """Each task result has all required fields."""
        from src.grader import _parse_llm_response
        fixture = load_fixture()
        result = _parse_llm_response(json.dumps(fixture))
        for task in result["tasks"]:
            assert "task_number" in task
            assert "page_number" in task
            assert "recognized_answer" in task
            assert "score" in task
            assert "max_score" in task
            assert "confidence" in task
            assert "grading_notes" in task


class TestMalformedJson:
    def test_malformed_json_raises(self):
        """Non-JSON response → json.JSONDecodeError propagated."""
        from src.grader import _parse_llm_response
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response("This is not JSON at all {{{")


class TestNullVariant:
    def test_null_variant_flagged(self):
        """detected_variant=null → result carries None detected_variant."""
        from src.grader import _parse_llm_response
        fixture = load_fixture()
        fixture["detected_variant"] = None
        result = _parse_llm_response(json.dumps(fixture))
        assert result["detected_variant"] is None


class TestGradeStudent:
    def test_grade_student_calls_litellm(self):
        """grade_student calls litellm.completion and returns parsed response."""
        from src.grader import grade_student
        fixture = load_fixture()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(fixture)

        with patch("src.grader.litellm.completion", return_value=mock_response) as mock_llm:
            result = grade_student(make_pages(), make_minimal_criteria())

        mock_llm.assert_called_once()
        assert result["detected_variant"] == 1
        assert len(result["tasks"]) == 16

    def test_grade_student_passes_all_images(self):
        """grade_student includes all page images in the LLM call."""
        from src.grader import grade_student
        fixture = load_fixture()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(fixture)

        pages = [(1, "base64page1"), (2, "base64page2"), (3, "base64page3")]

        with patch("src.grader.litellm.completion", return_value=mock_response) as mock_llm:
            grade_student(pages, make_minimal_criteria())

        call_args = mock_llm.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[0] if call_args.args else call_args.kwargs["messages"]
        # Find the user message content
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]
        image_items = [c for c in content if c.get("type") == "image_url"]
        assert len(image_items) == 3
