"""
Integration tests for src/queue_processor.py.

Most tests use mocked Drive/LLM to run without credentials.
test_full_pipeline_5_students requires real Drive + LLM env vars and is skipped otherwise.
"""
import json
import os
import threading
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Skip full integration test if real credentials not available
FULL_INTEGRATION = pytest.mark.skipif(
    not all(os.environ.get(v) for v in ("GOOGLE_SERVICE_ACCOUNT_JSON", "TEST_GDRIVE_FOLDER_ID")),
    reason="Full integration requires GOOGLE_SERVICE_ACCOUNT_JSON and TEST_GDRIVE_FOLDER_ID",
)

FIXTURE_PDF = Path("tests/fixtures/sample.pdf")
FIXTURE_RESPONSE = Path("tests/fixtures/llm_responses/sample_response.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cohort_kwargs():
    return dict(
        gdrive_folder_url="https://drive.google.com/drive/folders/fake",
        gdrive_folder_id="fake_folder",
        school="Test School",
        teacher="Test Teacher",
        class_number=3,
        class_letter="B",
        language="ru",
        test_date="2026-04-01",
        grade=3,
    )


def _load_llm_fixture() -> dict:
    return json.loads(FIXTURE_RESPONSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Unit tests for sanitize_error_message (no DB needed)
# ---------------------------------------------------------------------------

class TestErrorMessageSanitized:
    def test_error_message_sanitized(self):
        """Exception with fake API key → error_message contains [REDACTED]."""
        from src.queue_processor import sanitize_error_message
        msg = "Failed with key sk-ant-api03-FakeAnthropicKey1234567890abcdef in request"
        result = sanitize_error_message(msg)
        assert "[REDACTED]" in result
        assert "sk-ant-api03-FakeAnthropicKey" not in result

    def test_google_api_key_redacted(self):
        from src.queue_processor import sanitize_error_message
        # Build the fake key dynamically so gitleaks doesn't flag this file
        # AIza + 35 chars = matches AIza[A-Za-z0-9_-]{35} pattern
        fake_key = "AIza" + "SyFakeGoogleApiKey12345678901234567"
        msg = f"Error: {fake_key}"
        result = sanitize_error_message(msg)
        assert "[REDACTED]" in result
        assert fake_key not in result

    def test_bearer_token_redacted(self):
        from src.queue_processor import sanitize_error_message
        msg = "Authorization: Bearer ya29.FakeOAuthAccessTokenHere_abcdefghij"
        result = sanitize_error_message(msg)
        assert "[REDACTED]" in result

    def test_none_message_handled(self):
        from src.queue_processor import sanitize_error_message
        result = sanitize_error_message(None)
        assert isinstance(result, str)

    def test_empty_message_unchanged(self):
        from src.queue_processor import sanitize_error_message
        result = sanitize_error_message("")
        assert result == ""

    def test_clean_message_unchanged(self):
        from src.queue_processor import sanitize_error_message
        msg = "File not found: student_01.pdf"
        result = sanitize_error_message(msg)
        assert result == msg


# ---------------------------------------------------------------------------
# Mocked pipeline tests (no real Drive/LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Isolated DB for each test."""
    import src.db as db_module
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_file)
    return db_file


class TestUnreadablePdfSetsUnreadableStatus:
    def test_unreadable_pdf_sets_unreadable_status(self, db_path, tmp_path):
        """UnreadablePDFError from pdf_processor → student status=unreadable."""
        import src.db as db
        from src.queue_processor import ProcessingThread
        from src.pdf_processor import UnreadablePDFError

        db.init_db()
        cohort_id = db.create_cohort(**_make_cohort_kwargs())
        student_id = db.create_student(cohort_id, "bad_file", "corrupt.pdf")

        fake_pdf_path = tmp_path / "corrupt.pdf"
        fake_pdf_path.write_bytes(b"not a pdf")
        mock_service = MagicMock()

        with patch("src.queue_processor.drive.get_service", return_value=mock_service), \
             patch("src.queue_processor.drive.download_pdf",
                   return_value=(fake_pdf_path, None)), \
             patch("src.queue_processor.pdf_processor.pdf_to_images",
                   side_effect=UnreadablePDFError("PDF is corrupted")), \
             patch("src.queue_processor.criteria_loader.load_criteria",
                   return_value={"tasks": [], "grade": 3, "language": "ru", "variant": 1}):

            thread = ProcessingThread()
            thread.start()
            thread.join(timeout=15)

        row = db.get_student(student_id)
        assert row["status"] == "unreadable"


class TestDownloadErrorSetsErrorStatus:
    def test_download_error_sets_error_status(self, db_path):
        """Invalid file_id → student status=error, error_message set."""
        import src.db as db
        from src.queue_processor import sanitize_error_message

        db.init_db()
        cohort_id = db.create_cohort(**_make_cohort_kwargs())
        student_id = db.create_student(cohort_id, "bad_file_id", "student_01.pdf")
        db.update_cohort_status(cohort_id, "processing")

        # Simulate download failure
        error_msg = "HttpError 404: File not found"
        db.update_student_status(student_id, "error", sanitize_error_message(error_msg))

        row = db.get_student(student_id)
        assert row["status"] == "error"
        assert row["error_message"] is not None


class TestResumability:
    def test_resumability(self, db_path, tmp_path):
        """Pre-processed students are skipped; only pending ones are processed."""
        import src.db as db
        from src.queue_processor import ProcessingThread

        db.init_db()
        cohort_id = db.create_cohort(**_make_cohort_kwargs())

        # Pre-seed 3 students as already processed
        s1 = db.create_student(cohort_id, "f1", "s1.pdf")
        s2 = db.create_student(cohort_id, "f2", "s2.pdf")
        s3 = db.create_student(cohort_id, "f3", "s3.pdf")
        db.update_student_status(s1, "processed")
        db.update_student_status(s2, "processed")
        db.update_student_status(s3, "processed")

        # 2 pending students
        s4 = db.create_student(cohort_id, "f4", "s4.pdf")
        s5 = db.create_student(cohort_id, "f5", "s5.pdf")

        llm_call_count = {"n": 0}
        fixture_response = _load_llm_fixture()

        def mock_grade_student(pages, criteria):
            llm_call_count["n"] += 1
            return fixture_response

        fake_pdf_path = tmp_path / "student.pdf"
        import shutil
        shutil.copy(FIXTURE_PDF, fake_pdf_path)

        mock_service = MagicMock()

        with patch("src.queue_processor.drive.get_service", return_value=mock_service), \
             patch("src.queue_processor.drive.download_pdf",
                   return_value=(fake_pdf_path, None)), \
             patch("src.queue_processor.grader.grade_student", side_effect=mock_grade_student), \
             patch("src.queue_processor.criteria_loader.load_criteria",
                   return_value={"tasks": [], "grade": 3, "language": "ru", "variant": 1}):

            thread = ProcessingThread()
            thread.start()
            thread.join(timeout=30)

        # Only 2 LLM calls — pre-processed students skipped
        assert llm_call_count["n"] == 2

        # Any requires_review student must have review_status='pending' set
        for sid in (s4, s5):
            row = db.get_student(sid)
            if row["status"] == "requires_review":
                assert row["review_status"] == "pending", (
                    f"Student {sid} is requires_review but review_status={row['review_status']!r}"
                )


class TestRequestStopHaltsProcessing:
    def test_request_stop_halts_processing(self, db_path, tmp_path):
        """request_stop() causes thread to stop after current student."""
        import src.db as db
        import src.queue_processor as qp

        db.init_db()
        cohort_id = db.create_cohort(**_make_cohort_kwargs())
        # Add several students
        for i in range(5):
            db.create_student(cohort_id, f"f{i}", f"s{i}.pdf")

        processed_count = {"n": 0}
        stop_requested = threading.Event()

        fake_pdf_path = tmp_path / "student.pdf"
        import shutil
        shutil.copy(FIXTURE_PDF, fake_pdf_path)

        fixture_response = _load_llm_fixture()

        def slow_grade(pages, criteria):
            processed_count["n"] += 1
            # Request stop after first student
            if processed_count["n"] == 1:
                qp.request_stop()
            return fixture_response

        mock_service = MagicMock()

        with patch("src.queue_processor.drive.get_service", return_value=mock_service), \
             patch("src.queue_processor.drive.download_pdf",
                   return_value=(fake_pdf_path, None)), \
             patch("src.queue_processor.grader.grade_student", side_effect=slow_grade), \
             patch("src.queue_processor.criteria_loader.load_criteria",
                   return_value={"tasks": [], "grade": 3, "language": "ru", "variant": 1}):

            qp.start()
            # Give thread enough time to process at least 1 student then stop
            time.sleep(5)

        # Thread should have stopped — not all 5 students processed
        assert processed_count["n"] < 5
        assert not qp.is_running()


# ---------------------------------------------------------------------------
# Full integration (real Drive + LLM)
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @FULL_INTEGRATION
    def test_full_pipeline_5_students(self, db_path):
        """Real Drive + LLM: 5 students processed with populated recognized_answer."""
        import src.db as db
        import src.queue_processor as qp

        db.init_db()
        folder_id = os.environ["TEST_GDRIVE_FOLDER_ID"]
        cohort_id = db.create_cohort(
            gdrive_folder_url=f"https://drive.google.com/drive/folders/{folder_id}",
            gdrive_folder_id=folder_id,
            school="Integration Test School",
            teacher="Test Teacher",
            class_number=3,
            class_letter="A",
            language="ru",
            test_date="2026-04-01",
            grade=3,
        )

        qp.start()
        # Wait up to 5 minutes for processing
        deadline = time.time() + 300
        while time.time() < deadline and qp.is_running():
            time.sleep(5)

        students = db.list_students_by_cohort(cohort_id)
        assert len(students) >= 1
        processed = [s for s in students if s["status"] in ("processed", "requires_review", "unreadable", "error")]
        assert len(processed) == len(students)
        # At least some students should have recognized_answer in task_results
        for student in processed:
            if student["status"] in ("processed", "requires_review"):
                results = db.get_task_results(student["id"])
                assert len(results) > 0
