"""Unit tests for non-UI helper logic in pages/review_panel.py (Task 10)."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# resolve_pdf_path — path traversal guard
# ---------------------------------------------------------------------------

class TestPdfPathTraversalBlocked:
    def test_pdf_path_traversal_blocked(self, tmp_path):
        """Path outside base_dir raises ValueError."""
        from pages.review_panel import resolve_pdf_path

        base_dir = str(tmp_path / "data" / "downloads")
        Path(base_dir).mkdir(parents=True)

        with pytest.raises(ValueError, match="[Pp]ath traversal"):
            resolve_pdf_path(
                cohort_id=1,
                filename="../../etc/passwd",
                base_dir=base_dir,
            )

    def test_valid_path_resolves_correctly(self, tmp_path):
        """Normal cohort_id/filename returns correct absolute path."""
        from pages.review_panel import resolve_pdf_path

        base_dir = str(tmp_path / "data" / "downloads")
        Path(base_dir).mkdir(parents=True)
        cohort_dir = Path(base_dir) / "5"
        cohort_dir.mkdir()
        (cohort_dir / "student.pdf").write_bytes(b"fake")

        result = resolve_pdf_path(cohort_id=5, filename="student.pdf", base_dir=base_dir)
        assert result.name == "student.pdf"
        assert str(result).startswith(str(base_dir))


# ---------------------------------------------------------------------------
# score recalculation updates DB
# ---------------------------------------------------------------------------

class TestScoreRecalculationUpdatesDb:
    def test_score_recalculation_updates_db(self, tmp_path, monkeypatch):
        """Editing an answer calls compute_score() and saves result with manually_corrected=1."""
        import src.db as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
        db.init_db()

        cohort_id = db.create_cohort(
            gdrive_folder_url="https://drive.google.com/drive/folders/f",
            gdrive_folder_id="f",
            school="S", teacher="T", class_number=3, class_letter="A",
            language="ru", test_date="2026-04-01", grade=3,
        )
        student_id = db.create_student(cohort_id, "f1", "s1.pdf")
        db.save_task_result(
            student_id=student_id,
            task_number=1,
            page_number=1,
            recognized_answer="wrong",
            score=0.0,
            max_score=2.0,
            confidence="low",
        )

        task_criteria = {
            "task_number": 1,
            "answer_type": "text",
            "max_score": 2.0,
            "correct_answers": ["42"],
            "tiers": [
                {"label": "full", "score": 2.0, "condition": "Correct"},
                {"label": "zero", "score": 0.0, "condition": "Incorrect"},
            ],
        }

        from pages.review_panel import apply_score_update

        with patch("src.criteria_loader.load_criteria",
                   return_value={"tasks": [task_criteria]}):
            score, max_score = apply_score_update(
                student_id=student_id,
                task_number=1,
                new_answer="42",
                grade=3,
                language="ru",
                variant=1,
            )

        assert score == 2.0
        assert max_score == 2.0

        results = db.get_task_results(student_id)
        assert len(results) == 1
        assert results[0]["recognized_answer"] == "42"
        assert results[0]["manually_corrected"] == 1


# ---------------------------------------------------------------------------
# mark_done updates statuses
# ---------------------------------------------------------------------------

class TestMarkDoneUpdatesStatuses:
    def test_mark_done_updates_statuses(self, tmp_path, monkeypatch):
        """Marking a student done sets review_status='done' and status='processed'."""
        import src.db as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
        db.init_db()

        cohort_id = db.create_cohort(
            gdrive_folder_url="https://drive.google.com/drive/folders/f",
            gdrive_folder_id="f",
            school="S", teacher="T", class_number=3, class_letter="A",
            language="ru", test_date="2026-04-01", grade=3,
        )
        student_id = db.create_student(cohort_id, "f1", "s1.pdf")
        db.update_student_status(student_id, "requires_review")
        db.set_review_pending(student_id)

        db.mark_student_reviewed(student_id)

        row = db.get_student(student_id)
        assert row["status"] == "processed"
        assert row["review_status"] == "done"


# ---------------------------------------------------------------------------
# variant dropdown blocks task display until variant selected
# ---------------------------------------------------------------------------

class TestVariantDropdownBlocksTaskDisplay:
    def test_variant_dropdown_blocks_task_display(self):
        """Student with detected_variant=None should not show task edits."""
        from pages.review_panel import should_show_task_edits

        student_no_variant = {"detected_variant": None}
        student_with_variant = {"detected_variant": 1}

        assert should_show_task_edits(student_no_variant) is False
        assert should_show_task_edits(student_with_variant) is True
