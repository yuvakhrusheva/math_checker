"""Unit tests for non-UI helper logic in pages/main.py (Task 9)."""
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# validate_gdrive_url
# ---------------------------------------------------------------------------

class TestGDriveUrlValidation:
    def test_valid_url_returns_none(self):
        from pages.main import validate_gdrive_url
        assert validate_gdrive_url("https://drive.google.com/drive/folders/abc123") is None

    def test_gdrive_url_validation_invalid(self):
        """URL not containing 'drive.google.com' → validation error, cohort not added."""
        from pages.main import validate_gdrive_url
        error = validate_gdrive_url("https://dropbox.com/sh/xyz")
        assert error is not None
        assert isinstance(error, str)

    def test_empty_url_invalid(self):
        from pages.main import validate_gdrive_url
        error = validate_gdrive_url("")
        assert error is not None

    def test_none_url_invalid(self):
        from pages.main import validate_gdrive_url
        error = validate_gdrive_url(None)
        assert error is not None


# ---------------------------------------------------------------------------
# try_add_cohort — criteria check blocks cohort add
# ---------------------------------------------------------------------------

class TestCriteriaCheckBlocksCohortAdd:
    def test_criteria_check_blocks_cohort_add(self, tmp_path, monkeypatch):
        """If criteria_loader.criteria_exists() returns False, cohort is NOT created."""
        import src.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
        db_module.init_db()

        from pages.main import try_add_cohort

        with patch("src.criteria_loader.criteria_exists", return_value=False):
            cohort_id, error = try_add_cohort(
                gdrive_url="https://drive.google.com/drive/folders/fake123",
                school="Test School",
                teacher="Teacher A",
                class_number=3,
                class_letter="B",
                language="ru",
                test_date="2026-04-01",
                grade=3,
            )

        assert cohort_id is None
        assert error is not None
        assert len(db_module.list_cohorts()) == 0

    def test_valid_cohort_add_succeeds(self, tmp_path, monkeypatch):
        """Valid inputs with criteria present → cohort created with a student row per PDF."""
        import src.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
        db_module.init_db()

        from pages.main import try_add_cohort

        pdfs = [
            {"id": "file_1", "name": "student_01.pdf"},
            {"id": "file_2", "name": "student_02.pdf"},
        ]
        with patch("src.criteria_loader.criteria_exists", return_value=True), \
             patch("src.drive.extract_folder_id", return_value="fake123"), \
             patch("src.drive.get_service", return_value=object()), \
             patch("src.drive.list_pdfs", return_value=pdfs):
            cohort_id, error = try_add_cohort(
                gdrive_url="https://drive.google.com/drive/folders/fake123",
                school="Test School",
                teacher="Teacher A",
                class_number=3,
                class_letter="B",
                language="ru",
                test_date="2026-04-01",
                grade=3,
            )

        assert error is None
        assert cohort_id is not None
        assert len(db_module.list_cohorts()) == 1
        assert len(db_module.list_students_by_cohort(cohort_id)) == 2

    def test_empty_drive_folder_blocks_cohort_add(self, tmp_path, monkeypatch):
        """Drive folder with no PDFs → cohort is NOT created."""
        import src.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
        db_module.init_db()

        from pages.main import try_add_cohort

        with patch("src.criteria_loader.criteria_exists", return_value=True), \
             patch("src.drive.extract_folder_id", return_value="fake123"), \
             patch("src.drive.get_service", return_value=object()), \
             patch("src.drive.list_pdfs", return_value=[]):
            cohort_id, error = try_add_cohort(
                gdrive_url="https://drive.google.com/drive/folders/fake123",
                school="Test School",
                teacher="Teacher A",
                class_number=3,
                class_letter="B",
                language="ru",
                test_date="2026-04-01",
                grade=3,
            )

        assert cohort_id is None
        assert error is not None
        assert "No PDF files" in error
        assert len(db_module.list_cohorts()) == 0

    def test_drive_failure_blocks_cohort_add(self, tmp_path, monkeypatch):
        """Drive API error → cohort is NOT created and the error surfaces."""
        import src.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
        db_module.init_db()

        from pages.main import try_add_cohort

        with patch("src.criteria_loader.criteria_exists", return_value=True), \
             patch("src.drive.extract_folder_id", return_value="fake123"), \
             patch("src.drive.get_service", side_effect=RuntimeError("boom")):
            cohort_id, error = try_add_cohort(
                gdrive_url="https://drive.google.com/drive/folders/fake123",
                school="Test School",
                teacher="Teacher A",
                class_number=3,
                class_letter="B",
                language="ru",
                test_date="2026-04-01",
                grade=3,
            )

        assert cohort_id is None
        assert error is not None
        assert "Could not read Drive folder" in error
        assert len(db_module.list_cohorts()) == 0


# ---------------------------------------------------------------------------
# should_disable_start_button
# ---------------------------------------------------------------------------

class TestStartButtonDisabledWhenRunning:
    def test_start_button_disabled_when_running(self):
        """When queue_processor.is_running() returns True, start button is disabled."""
        from pages.main import should_disable_start_button

        with patch("src.queue_processor.is_running", return_value=True):
            assert should_disable_start_button() is True

    def test_start_button_enabled_when_not_running(self):
        from pages.main import should_disable_start_button

        with patch("src.queue_processor.is_running", return_value=False):
            assert should_disable_start_button() is False


# ---------------------------------------------------------------------------
# edit blocked for non-pending cohort (enforced by db.py)
# ---------------------------------------------------------------------------

class TestEditBlockedForNonPendingCohort:
    def test_edit_blocked_for_non_pending_cohort(self, tmp_path, monkeypatch):
        """update_cohort_metadata on a non-pending cohort raises ValueError."""
        import src.db as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
        db.init_db()

        cohort_id = db.create_cohort(
            gdrive_folder_url="https://drive.google.com/drive/folders/f",
            gdrive_folder_id="f",
            school="School",
            teacher="Teacher",
            class_number=3,
            class_letter="A",
            language="ru",
            test_date="2026-04-01",
            grade=3,
        )
        db.update_cohort_status(cohort_id, "processing")

        with pytest.raises(ValueError, match="must be 'pending'"):
            db.update_cohort_metadata(cohort_id, school="New School")
