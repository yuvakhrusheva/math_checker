"""
Tests for app.py startup validation of GOOGLE_SERVICE_ACCOUNT_JSON env var.
These tests verify fail-fast behaviour before any Streamlit UI is rendered.
"""
import os
import pytest
import importlib
import sys


def _reload_validate():
    """Import validate_config fresh each time (env vars change between tests)."""
    if "app" in sys.modules:
        del sys.modules["app"]
    import app
    return app.validate_config


class TestMissingEnvVar:
    def test_missing_env_var_raises_error(self, monkeypatch):
        """When GOOGLE_SERVICE_ACCOUNT_JSON is not set, validate_config raises ValueError."""
        monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
        validate_config = _reload_validate()
        with pytest.raises((ValueError, SystemExit)) as exc_info:
            validate_config()
        # Error message should be informative
        msg = str(exc_info.value).lower()
        assert "google_service_account_json" in msg or "service account" in msg or "google" in msg

    def test_empty_string_env_var_raises_error(self, monkeypatch):
        """Empty string is treated as not set."""
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        validate_config = _reload_validate()
        with pytest.raises((ValueError, SystemExit)):
            validate_config()


class TestInvalidPath:
    def test_relative_path_raises_error(self, monkeypatch):
        """A relative path raises a clear error (must be absolute)."""
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "keys/service_account.json")
        validate_config = _reload_validate()
        with pytest.raises((ValueError, SystemExit)) as exc_info:
            validate_config()
        msg = str(exc_info.value).lower()
        assert "absolute" in msg or "relative" in msg or "path" in msg

    def test_nonexistent_file_raises_error(self, monkeypatch, tmp_path):
        """An absolute path to a non-existent file raises a clear error."""
        nonexistent = str(tmp_path / "does_not_exist.json")
        assert os.path.isabs(nonexistent)
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", nonexistent)
        validate_config = _reload_validate()
        with pytest.raises((ValueError, SystemExit)) as exc_info:
            validate_config()
        msg = str(exc_info.value).lower()
        assert "not found" in msg or "does not exist" in msg or "exist" in msg or "file" in msg

    def test_valid_absolute_path_passes(self, monkeypatch, tmp_path):
        """A valid absolute path to an existing file passes validation."""
        key_file = tmp_path / "service_account.json"
        key_file.write_text('{"type": "service_account"}')
        monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", str(key_file))
        validate_config = _reload_validate()
        # Should not raise
        validate_config()
