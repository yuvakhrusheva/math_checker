"""
Unit tests for src/criteria_loader.py

TDD anchors — written BEFORE implementation.
All 7 tests must fail before criteria_loader.py is implemented.
"""
import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_criteria_bytes(grade: int, language: str, variant: int) -> bytes:
    """Return minimal valid criteria JSON as bytes."""
    data = {
        "grade": grade,
        "language": language,
        "variant": variant,
        "total_max_score": 100,
        "tasks": [
            {
                "task_number": 1,
                "description": "Order numbers ascending",
                "max_score": 4,
                "tiers": [
                    {"score": 4, "label": "full", "condition": "All correct"},
                    {"score": 0, "label": "zero", "condition": "Any wrong"},
                ],
                "correct_answers": ["93, 309, 390, 930"],
                "answer_type": "ordered_list",
            }
        ],
    }
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# validate_criteria_file tests
# ---------------------------------------------------------------------------

def test_validate_valid_file():
    """
    A valid filename with matching internal fields should return the parsed dict.
    """
    from src.criteria_loader import validate_criteria_file

    filename = "grade3_ru_v1.json"
    content = _make_criteria_bytes(grade=3, language="ru", variant=1)

    result = validate_criteria_file(filename, content)

    assert isinstance(result, dict)
    assert result["grade"] == 3
    assert result["language"] == "ru"
    assert result["variant"] == 1


def test_validate_filename_mismatch():
    """
    filename says grade3, but JSON says grade2 → ValueError with descriptive message.
    """
    from src.criteria_loader import validate_criteria_file

    filename = "grade3_ru_v1.json"
    content = _make_criteria_bytes(grade=2, language="ru", variant=1)  # mismatch!

    with pytest.raises(ValueError, match="grade"):
        validate_criteria_file(filename, content)


def test_validate_invalid_filename_format():
    """
    A filename that does not match the pattern grade[23]_(ru|az)_v[12].json → ValueError.
    """
    from src.criteria_loader import validate_criteria_file

    filename = "math_test_grade3.json"  # wrong naming pattern
    content = _make_criteria_bytes(grade=3, language="ru", variant=1)

    with pytest.raises(ValueError, match="[Ff]ilename"):
        validate_criteria_file(filename, content)


def test_validate_invalid_json():
    """Invalid JSON content raises ValueError."""
    from src.criteria_loader import validate_criteria_file

    with pytest.raises(ValueError, match="(?i)json"):
        validate_criteria_file("grade3_ru_v1.json", b"this is not json {{")


# ---------------------------------------------------------------------------
# save_criteria_file tests
# ---------------------------------------------------------------------------

def test_save_path_traversal_blocked(monkeypatch, tmp_path):
    """
    A filename like '../../.env' must raise ValueError (path traversal protection).
    """
    from src.criteria_loader import save_criteria_file

    # change cwd so Path('criteria') points inside tmp_path
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="[Pp]ath"):
        save_criteria_file("../../.env", b"anything")


# ---------------------------------------------------------------------------
# list_available_combinations tests
# ---------------------------------------------------------------------------

def test_list_available_combinations(monkeypatch, tmp_path):
    """
    Should return correct list of grade/language/variant dicts based on files in criteria/.
    """
    from src.criteria_loader import list_available_combinations

    # Set up a fake criteria dir with known valid files
    criteria_dir = tmp_path / "criteria"
    criteria_dir.mkdir()

    # Write two valid criteria files
    (criteria_dir / "grade3_ru_v1.json").write_bytes(
        _make_criteria_bytes(3, "ru", 1)
    )
    (criteria_dir / "grade2_az_v2.json").write_bytes(
        _make_criteria_bytes(2, "az", 2)
    )
    # Write one file with wrong naming — should be ignored
    (criteria_dir / "not_a_criteria.json").write_bytes(b"{}")

    monkeypatch.chdir(tmp_path)

    result = list_available_combinations()

    assert isinstance(result, list)
    assert len(result) == 2

    grades = {r["grade"] for r in result}
    languages = {r["language"] for r in result}
    variants = {r["variant"] for r in result}

    assert grades == {2, 3}
    assert languages == {"ru", "az"}
    assert variants == {1, 2}


# ---------------------------------------------------------------------------
# criteria_exists tests
# ---------------------------------------------------------------------------

def test_criteria_exists_true(monkeypatch, tmp_path):
    """
    criteria_exists should return True when at least one variant file exists.
    """
    from src.criteria_loader import criteria_exists

    criteria_dir = tmp_path / "criteria"
    criteria_dir.mkdir()
    (criteria_dir / "grade3_ru_v1.json").write_bytes(
        _make_criteria_bytes(3, "ru", 1)
    )

    monkeypatch.chdir(tmp_path)

    assert criteria_exists(3, "ru") is True


def test_criteria_exists_false(monkeypatch, tmp_path):
    """
    criteria_exists should return False when no file exists for grade+language combo.
    """
    from src.criteria_loader import criteria_exists

    criteria_dir = tmp_path / "criteria"
    criteria_dir.mkdir()
    # Only grade3_ru — not grade2_az
    (criteria_dir / "grade3_ru_v1.json").write_bytes(
        _make_criteria_bytes(3, "ru", 1)
    )

    monkeypatch.chdir(tmp_path)

    assert criteria_exists(2, "az") is False


def test_load_criteria_missing_file(monkeypatch, tmp_path):
    """load_criteria raises FileNotFoundError when the file does not exist."""
    from src.criteria_loader import load_criteria

    criteria_dir = tmp_path / "criteria"
    criteria_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError):
        load_criteria(3, "ru", 1)
