"""Unit tests for src/exporter.py (Task 11 — TDD)."""
import re
import pytest
import pandas as pd
from pathlib import Path


def _setup_db(db, tmp_path):
    """Helper: init DB and return a function to create a student with task results."""
    import src.db as db_module
    db_module.init_db()

    def make_cohort(school="School A", class_number=3, class_letter="A",
                    teacher="Teacher", test_date="2026-04-01", grade=3, language="ru"):
        return db_module.create_cohort(
            gdrive_folder_url="https://drive.google.com/drive/folders/f",
            gdrive_folder_id="f",
            school=school,
            teacher=teacher,
            class_number=class_number,
            class_letter=class_letter,
            language=language,
            test_date=test_date,
            grade=grade,
        )

    def make_student(cohort_id, status="processed", review_status=None,
                     recognized_name="Student Name", num_tasks=16):
        student_id = db_module.create_student(cohort_id, f"file_{student_id_counter[0]}", "s.pdf")
        student_id_counter[0] += 1
        db_module.update_student_status(student_id, status)
        if recognized_name:
            db_module.update_student_recognized_name(student_id, recognized_name)
        if review_status:
            from src.db import get_connection
            with get_connection() as conn:
                conn.execute(
                    "UPDATE students SET review_status = ? WHERE id = ?",
                    (review_status, student_id)
                )
        if status not in ("error", "unreadable"):
            for t in range(1, num_tasks + 1):
                db_module.save_task_result(
                    student_id=student_id,
                    task_number=t,
                    page_number=1,
                    recognized_answer=f"answer_{t}",
                    score=1.0,
                    max_score=2.0,
                    confidence="high",
                )
        return student_id

    student_id_counter = [1]
    return make_cohort, make_student


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    import src.db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
    return db_module, tmp_path


# ---------------------------------------------------------------------------
# test_two_sheets_created
# ---------------------------------------------------------------------------

class TestTwoSheetsCreated:
    def test_two_sheets_created(self, db_env, tmp_path):
        """Output file has 'Answers' and 'Scores' sheets."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)

        cohort_id = make_cohort()
        make_student(cohort_id)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        xl = pd.ExcelFile(path)
        assert "Answers" in xl.sheet_names
        assert "Scores" in xl.sheet_names


# ---------------------------------------------------------------------------
# test_answers_sheet_columns
# ---------------------------------------------------------------------------

class TestAnswersSheetColumns:
    def test_answers_sheet_columns(self, db_env, tmp_path):
        """Answers sheet has all required columns including 16 task answer columns."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        make_student(cohort_id)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        expected_cols = [
            "school", "class", "teacher", "test_date", "student_id", "recognized_name",
        ] + [f"task_{i}_answer" for i in range(1, 17)]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# test_scores_sheet_columns
# ---------------------------------------------------------------------------

class TestScoresSheetColumns:
    def test_scores_sheet_columns(self, db_env, tmp_path):
        """Scores sheet has all required columns including total_score and performance_level."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        make_student(cohort_id)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Scores")
        expected_cols = [
            "school", "class", "teacher", "test_date", "student_id",
        ] + [f"task_{i}_score" for i in range(1, 17)] + ["total_score", "performance_level"]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# test_unreadable_students_excluded
# ---------------------------------------------------------------------------

class TestUnreadableStudentsExcluded:
    def test_unreadable_students_excluded(self, db_env, tmp_path):
        """Students with status=unreadable are not in the output."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        sid_ok = make_student(cohort_id, status="processed")
        sid_bad = make_student(cohort_id, status="unreadable")

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        ids_in_output = set(df["student_id"].tolist())
        assert sid_ok in ids_in_output
        assert sid_bad not in ids_in_output


# ---------------------------------------------------------------------------
# test_error_students_have_ERROR_marker
# ---------------------------------------------------------------------------

class TestErrorStudentsHaveErrorMarker:
    def test_error_students_have_ERROR_marker(self, db_env, tmp_path):
        """Students with status=error show 'ERROR' in all task columns."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        sid = make_student(cohort_id, status="error")

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        row = df[df["student_id"] == sid].iloc[0]
        for t in range(1, 17):
            assert row[f"task_{t}_answer"] == "ERROR", f"task_{t}_answer should be ERROR"

        df_scores = pd.read_excel(path, sheet_name="Scores")
        row_s = df_scores[df_scores["student_id"] == sid].iloc[0]
        for t in range(1, 17):
            assert row_s[f"task_{t}_score"] == "ERROR", f"task_{t}_score should be ERROR"


# ---------------------------------------------------------------------------
# test_pending_review_shows_PENDING
# ---------------------------------------------------------------------------

class TestPendingReviewShowsPending:
    def test_pending_review_shows_PENDING(self, db_env, tmp_path):
        """Students with review_status=pending show 'PENDING' in all task columns."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        sid = make_student(cohort_id, status="requires_review", review_status="pending")

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        row = df[df["student_id"] == sid].iloc[0]
        for t in range(1, 17):
            assert row[f"task_{t}_answer"] == "PENDING", f"task_{t}_answer should be PENDING"

        df_scores = pd.read_excel(path, sheet_name="Scores")
        row_s = df_scores[df_scores["student_id"] == sid].iloc[0]
        for t in range(1, 17):
            assert row_s[f"task_{t}_score"] == "PENDING", f"task_{t}_score should be PENDING"


# ---------------------------------------------------------------------------
# test_sort_order
# ---------------------------------------------------------------------------

class TestSortOrder:
    def test_sort_order(self, db_env, tmp_path):
        """Rows sorted school → class → student_id."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)

        c1 = make_cohort(school="Zoo School", class_number=3, class_letter="A")
        c2 = make_cohort(school="Alpha School", class_number=2, class_letter="B")
        c3 = make_cohort(school="Alpha School", class_number=3, class_letter="A")

        s_zoo = make_student(c1)
        s_alpha_2b_1 = make_student(c2)
        s_alpha_2b_2 = make_student(c2)
        s_alpha_3a = make_student(c3)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        schools = df["school"].tolist()
        # Alpha School rows should come before Zoo School
        first_zoo = schools.index("Zoo School")
        last_alpha = max(i for i, s in enumerate(schools) if s == "Alpha School")
        assert last_alpha < first_zoo

        # Within Alpha School, 2B before 3A
        alpha_rows = df[df["school"] == "Alpha School"].reset_index(drop=True)
        classes = alpha_rows["class"].tolist()
        assert classes.index("2B") < classes.index("3A")

        # Within same cohort, lower student_id first
        s2b_rows = df[(df["school"] == "Alpha School") & (df["class"] == "2B")]
        ids = s2b_rows["student_id"].tolist()
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# test_grade3_performance_levels
# ---------------------------------------------------------------------------

class TestGrade3PerformanceLevels:
    @pytest.mark.parametrize("total,expected", [
        (0.0, "Insufficient"),
        (30.0, "Insufficient"),
        (31.0, "Minimal"),
        (60.0, "Minimal"),
        (61.0, "Basic"),
        (80.0, "Basic"),
        (81.0, "Advanced"),
        (100.0, "Advanced"),
    ])
    def test_grade3_performance_levels(self, db_env, tmp_path, total, expected):
        """Correct performance level boundaries for grade 3."""
        from src.exporter import _performance_level
        assert _performance_level(total) == expected


# ---------------------------------------------------------------------------
# test_grade2_performance_level_blank
# ---------------------------------------------------------------------------

class TestRequiresReviewNullReviewStatusIsPending:
    def test_requires_review_null_review_status_shows_PENDING(self, db_env, tmp_path):
        """requires_review student with NULL review_status shows PENDING (not real data)."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        # review_status left as None (NULL in DB)
        sid = make_student(cohort_id, status="requires_review", review_status=None)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Answers")
        row = df[df["student_id"] == sid].iloc[0]
        assert row["task_1_answer"] == "PENDING"


class TestGrade2PerformanceLevelBlank:
    def test_grade2_performance_level_blank(self, db_env, tmp_path):
        """Grade 2 rows have empty string in performance_level."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort(grade=2)
        sid = make_student(cohort_id)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        df = pd.read_excel(path, sheet_name="Scores")
        row = df[df["student_id"] == sid].iloc[0]
        # performance_level should be blank (empty string or NaN, not a level name)
        val = row["performance_level"]
        assert val == "" or (val != val)  # empty string or NaN


# ---------------------------------------------------------------------------
# test_filename_format
# ---------------------------------------------------------------------------

class TestFilenameFormat:
    def test_filename_format(self, db_env, tmp_path):
        """Filename matches results_YYYY-MM-DD_HH-MM.xlsx."""
        db_module, _ = db_env
        make_cohort, make_student = _setup_db(db_module, tmp_path)
        cohort_id = make_cohort()
        make_student(cohort_id)

        from src.exporter import export
        path = export(output_dir=str(tmp_path / "out"))

        pattern = re.compile(r"results_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.xlsx$")
        assert pattern.search(path.name), f"Filename {path.name!r} doesn't match pattern"
