"""
Unit tests for src/db.py — TDD anchors.
All tests use a temporary SQLite file (tmp_path fixture) and
monkey-patch DB_PATH so the real data/ directory is never touched.
"""

import sqlite3
import pytest

import src.db as db_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cohort_kwargs():
    return dict(
        gdrive_folder_url="https://drive.google.com/drive/folders/abc",
        gdrive_folder_id="abc",
        school="School 1",
        teacher="Teacher A",
        class_number=2,
        class_letter="A",
        language="ru",
        test_date="2026-04-01",
        grade=2,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_db_path(tmp_path, monkeypatch):
    """Redirect every test to an isolated temporary DB file."""
    db_file = tmp_path / "test_math_checker.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))
    # Also patch the data dir creation to point at tmp_path
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    yield db_file


# ---------------------------------------------------------------------------
# TDD Anchor 1: init_db creates all three tables
# ---------------------------------------------------------------------------

def test_init_db_creates_tables():
    db_module.init_db()
    with sqlite3.connect(db_module.DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
    assert "cohorts" in tables
    assert "students" in tables
    assert "task_results" in tables


# ---------------------------------------------------------------------------
# TDD Anchor 2: init_db is idempotent (no error on second call)
# ---------------------------------------------------------------------------

def test_init_db_idempotent():
    db_module.init_db()
    db_module.init_db()  # Must not raise


# ---------------------------------------------------------------------------
# TDD Anchor 3: create_cohort inserts a row with correct fields
# ---------------------------------------------------------------------------

def test_create_cohort():
    db_module.init_db()
    kwargs = _make_cohort_kwargs()
    cohort_id = db_module.create_cohort(**kwargs)
    assert cohort_id == 1

    row = db_module.get_cohort(cohort_id)
    assert row is not None
    assert row["gdrive_folder_id"] == "abc"
    assert row["school"] == "School 1"
    assert row["teacher"] == "Teacher A"
    assert row["class_number"] == 2
    assert row["class_letter"] == "A"
    assert row["language"] == "ru"
    assert row["test_date"] == "2026-04-01"
    assert row["grade"] == 2
    assert row["status"] == "pending"
    assert row["in_project"] == 1


# ---------------------------------------------------------------------------
# TDD Anchor 4: update_cohort_metadata raises error when status != pending
# ---------------------------------------------------------------------------

def test_update_cohort_metadata_blocked_when_processing():
    db_module.init_db()
    cohort_id = db_module.create_cohort(**_make_cohort_kwargs())
    db_module.update_cohort_status(cohort_id, "processing")

    with pytest.raises(ValueError, match="pending"):
        db_module.update_cohort_metadata(cohort_id, school="New School")


# ---------------------------------------------------------------------------
# TDD Anchor 5: create_student inserts row linked to cohort
# ---------------------------------------------------------------------------

def test_create_student():
    db_module.init_db()
    cohort_id = db_module.create_cohort(**_make_cohort_kwargs())
    student_id = db_module.create_student(
        cohort_id=cohort_id,
        gdrive_file_id="file_xyz",
        filename="student_01.jpg",
    )
    assert student_id == 1

    row = db_module.get_student(student_id)
    assert row is not None
    assert row["cohort_id"] == cohort_id
    assert row["gdrive_file_id"] == "file_xyz"
    assert row["filename"] == "student_01.jpg"
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# TDD Anchor 6: save_task_result UNIQUE constraint — INSERT OR REPLACE behavior
# ---------------------------------------------------------------------------

def test_save_task_result_unique_constraint():
    """
    save_task_result uses INSERT OR REPLACE, so a second call with the same
    (student_id, task_number) must silently overwrite rather than raise.
    We also verify the UNIQUE constraint itself by attempting a raw INSERT
    of a duplicate, which must raise IntegrityError.
    """
    db_module.init_db()
    cohort_id = db_module.create_cohort(**_make_cohort_kwargs())
    student_id = db_module.create_student(
        cohort_id=cohort_id,
        gdrive_file_id="file_xyz",
        filename="student_01.jpg",
    )

    # First save — should succeed
    db_module.save_task_result(
        student_id=student_id,
        task_number=1,
        page_number=1,
        recognized_answer="5",
        score=1.0,
        max_score=1.0,
        confidence="high",
    )

    # Second save with same (student_id, task_number) — INSERT OR REPLACE must NOT raise
    db_module.save_task_result(
        student_id=student_id,
        task_number=1,
        page_number=1,
        recognized_answer="6",
        score=0.0,
        max_score=1.0,
        confidence="low",
    )

    # The row must now reflect the updated values
    results = db_module.get_task_results(student_id)
    assert len(results) == 1
    assert results[0]["recognized_answer"] == "6"
    assert results[0]["confidence"] == "low"

    # Verify the UNIQUE constraint exists: raw INSERT (not OR REPLACE) must fail
    with db_module.get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_results "
                "(student_id, task_number, page_number, score, max_score, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (student_id, 1, 1, 0.5, 1.0, "high"),
            )


# ---------------------------------------------------------------------------
# TDD Anchor 7: list_requires_review returns only requires_review students
# ---------------------------------------------------------------------------

def test_list_requires_review():
    db_module.init_db()
    cohort_id = db_module.create_cohort(**_make_cohort_kwargs())

    s1 = db_module.create_student(cohort_id, "f1", "s1.jpg")
    s2 = db_module.create_student(cohort_id, "f2", "s2.jpg")
    s3 = db_module.create_student(cohort_id, "f3", "s3.jpg")

    db_module.update_student_status(s1, "requires_review")
    db_module.update_student_status(s2, "processed")
    db_module.update_student_status(s3, "requires_review")

    rows = db_module.list_requires_review()
    ids = {r["id"] for r in rows}
    assert s1 in ids
    assert s3 in ids
    assert s2 not in ids
