"""
Tests for app.py startup validation of GOOGLE_SERVICE_ACCOUNT_JSON env var.
These tests verify fail-fast behaviour before any Streamlit UI is rendered.
"""
import os
import sys
import pytest


def _get_validate_config():
    """Re-import validate_config fresh each test (avoids cached module state)."""
    if "app" in sys.modules:
        del sys.modules["app"]
    from unittest.mock import patch
    # Prevent load_dotenv() from loading a real .env that overrides monkeypatched env vars
    with patch("dotenv.load_dotenv"):
        import app
    return app.validate_config


class TestMissingEnvVar:
    def test_missing_env_var_raises_error(self, monkeypatch):
        """When GOOGLE_SERVICE_ACCOUNT_JSON is not set, validate_config raises ValueError."""
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
        validate_config = _get_validate_config()
        with pytest.raises(ValueError, match=r"(?i)GOOGLE_SERVICE_ACCOUNT_JSON|service account"):
            validate_config()

    def test_empty_string_env_var_raises_error(self, monkeypatch):
        """Empty string is treated as not set."""
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        validate_config = _get_validate_config()
        with pytest.raises(ValueError):
            validate_config()

    def test_whitespace_only_env_var_raises_error(self, monkeypatch):
        """Whitespace-only value is treated as not set."""
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "   ")
        validate_config = _get_validate_config()
        with pytest.raises(ValueError):
            validate_config()


class TestInvalidPath:
    def test_relative_path_raises_error(self, monkeypatch):
        """A relative path raises a clear error (must be absolute)."""
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "keys/service_account.json")
        validate_config = _get_validate_config()
        with pytest.raises(ValueError, match=r"(?i)absolute|relative"):
            validate_config()

    def test_nonexistent_file_raises_error(self, monkeypatch, tmp_path):
        """An absolute path to a non-existent file raises a clear error."""
        nonexistent = str(tmp_path / "does_not_exist.json")
        assert os.path.isabs(nonexistent)
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", nonexistent)
        validate_config = _get_validate_config()
        with pytest.raises(ValueError, match=r"(?i)not exist|does not exist|file"):
            validate_config()

    def test_path_inside_repo_raises_error(self, monkeypatch, tmp_path):
        """A path inside the repo root raises an error (key must be outside repo)."""
        # Create a file inside the project directory
        key_file = tmp_path / "service_account.json"
        key_file.write_text('{"type": "service_account"}')
        # Patch repo root to be tmp_path so the key appears to be inside it
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(key_file))
        validate_config = _get_validate_config()
        import unittest.mock as mock
        with mock.patch("os.path.dirname", return_value=str(tmp_path)):
            with pytest.raises(ValueError, match=r"(?i)outside|repo"):
                validate_config()

    def test_valid_absolute_path_outside_repo_passes(self, monkeypatch, tmp_path):
        """A valid absolute path to an existing file outside the repo passes validation."""
        key_file = tmp_path / "service_account.json"
        key_file.write_text('{"type": "service_account"}')
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(key_file))
        validate_config = _get_validate_config()
        # Should not raise (tmp_path is outside the actual repo root)
        validate_config()
