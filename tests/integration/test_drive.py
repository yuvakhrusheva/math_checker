"""
Tests for src/drive.py.

Unit tests (URL parsing, filename sanitization) run without network.
Integration tests require environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON — absolute path to service account key file
  TEST_GDRIVE_FOLDER_ID       — Google Drive folder ID containing test PDFs
  TEST_GDRIVE_PDF_COUNT       — (optional) expected number of PDFs in test folder
"""
import os
import pytest

# Integration marker — skip if TEST env vars are not set
INTEGRATION_ENV_VARS = ("GOOGLE_SERVICE_ACCOUNT_JSON", "TEST_GDRIVE_FOLDER_ID")
integration = pytest.mark.skipif(
    not all(os.environ.get(v) for v in INTEGRATION_ENV_VARS),
    reason="Integration tests require GOOGLE_SERVICE_ACCOUNT_JSON and TEST_GDRIVE_FOLDER_ID env vars",
)


# ---------------------------------------------------------------------------
# Unit tests — no network required
# ---------------------------------------------------------------------------

class TestExtractFolderIdVariousUrlFormats:
    """test_extract_folder_id_various_url_formats (TDD anchor)."""

    def test_standard_url(self):
        from src.drive import extract_folder_id
        url = "https://drive.google.com/drive/folders/1ABCdefGHIjklMNOpqrSTUvwxyz"
        assert extract_folder_id(url) == "1ABCdefGHIjklMNOpqrSTUvwxyz"

    def test_url_with_u0(self):
        from src.drive import extract_folder_id
        url = "https://drive.google.com/drive/u/0/folders/1ABCdefGHIjklMNOpqrSTUvwxyz"
        assert extract_folder_id(url) == "1ABCdefGHIjklMNOpqrSTUvwxyz"

    def test_url_with_u1(self):
        from src.drive import extract_folder_id
        url = "https://drive.google.com/drive/u/1/folders/1FOLDER_ID_123"
        assert extract_folder_id(url) == "1FOLDER_ID_123"

    def test_url_with_query_params(self):
        from src.drive import extract_folder_id
        url = "https://drive.google.com/drive/folders/1ABCdef?usp=sharing"
        assert extract_folder_id(url) == "1ABCdef"

    def test_bare_folder_id_passthrough(self):
        """Plain folder ID (no URL) returned as-is."""
        from src.drive import extract_folder_id
        folder_id = "1ABCdefGHIjklMNOpqrSTUvwxyz"
        assert extract_folder_id(folder_id) == folder_id

    def test_invalid_url_raises(self):
        from src.drive import extract_folder_id
        with pytest.raises(ValueError, match="(?i)folder|url"):
            extract_folder_id("https://docs.google.com/document/d/something/edit")


class TestFilenameSanitization:
    """test_filename_sanitization (TDD anchor)."""

    def test_plain_filename_unchanged(self):
        from src.drive import sanitize_filename
        assert sanitize_filename("student_01.pdf") == "student_01.pdf"

    def test_path_separators_removed(self):
        from src.drive import sanitize_filename
        result = sanitize_filename("../../etc/passwd.pdf")
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result

    def test_unix_path_basename(self):
        from src.drive import sanitize_filename
        result = sanitize_filename("/var/secret/test.pdf")
        assert result == "test.pdf"

    def test_windows_path_basename(self):
        from src.drive import sanitize_filename
        result = sanitize_filename("C:\\Users\\student\\test.pdf")
        assert result == "test.pdf"

    def test_dangerous_chars_replaced(self):
        from src.drive import sanitize_filename
        result = sanitize_filename("student: test<1>.pdf")
        assert ":" not in result
        assert "<" not in result
        assert ">" not in result

    def test_empty_name_fallback(self):
        from src.drive import sanitize_filename
        result = sanitize_filename("")
        assert len(result) > 0  # should produce a fallback name


# ---------------------------------------------------------------------------
# Integration tests — require real Google Drive credentials
# ---------------------------------------------------------------------------

class TestDownloadPdfFailure:
    """Unit test for CA-14: download failure returns error dict, does not raise."""

    def test_download_failure_returns_error_dict(self, tmp_path):
        """download_pdf returns (None, error_dict) when Drive raises an error."""
        from src.drive import download_pdf
        from unittest.mock import MagicMock
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        # Simulate HttpError on get_media
        fake_resp = MagicMock()
        fake_resp.status = 403
        error = HttpError(resp=fake_resp, content=b"Forbidden")
        mock_service.files().get_media.side_effect = error

        path, err = download_pdf(mock_service, "file123", "test.pdf", 1, tmp_path)
        assert path is None
        assert err is not None
        assert err["file_id"] == "file123"
        assert "error" in err

    def test_list_pdfs_empty_folder(self):
        """list_pdfs returns [] when folder has no PDFs."""
        from src.drive import list_pdfs
        from unittest.mock import MagicMock

        mock_service = MagicMock()
        mock_service.files().list().execute.return_value = {"files": []}

        result = list_pdfs(mock_service, "folder123")
        assert result == []


class TestGetService:
    @integration
    def test_get_service_success(self):
        """Service created without error (requires TEST env vars)."""
        from src.drive import get_service
        svc = get_service()
        assert svc is not None


class TestListPdfs:
    @integration
    def test_list_pdfs_returns_correct_count(self):
        """Lists PDFs in test folder; count matches TEST_GDRIVE_PDF_COUNT if set."""
        from src.drive import get_service, list_pdfs
        folder_id = os.environ["TEST_GDRIVE_FOLDER_ID"]
        svc = get_service()
        pdfs = list_pdfs(svc, folder_id)
        assert isinstance(pdfs, list)
        expected_count = os.environ.get("TEST_GDRIVE_PDF_COUNT")
        if expected_count:
            assert len(pdfs) == int(expected_count)
        for item in pdfs:
            assert "id" in item
            assert "name" in item


class TestDownloadPdf:
    @integration
    def test_download_pdf_file_size_matches(self, tmp_path):
        """Downloaded file size matches Drive metadata size."""
        from src.drive import get_service, list_pdfs, download_pdf
        folder_id = os.environ["TEST_GDRIVE_FOLDER_ID"]
        svc = get_service()
        pdfs = list_pdfs(svc, folder_id)
        if not pdfs:
            pytest.skip("No PDFs in test folder")
        first_pdf = pdfs[0]
        cohort_id = 999
        result = download_pdf(svc, first_pdf["id"], first_pdf["name"], cohort_id, tmp_path)
        assert result is not None
        file_path, error_info = result
        assert error_info is None
        assert file_path is not None
        assert file_path.exists()
        # If Drive reports size, verify it matches
        if "size" in first_pdf and first_pdf["size"]:
            assert file_path.stat().st_size == int(first_pdf["size"])
