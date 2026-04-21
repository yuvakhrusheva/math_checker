"""Google Drive integration for math_checker.

Provides:
  get_service()        — authenticate and build Drive API v3 client
  extract_folder_id()  — parse folder ID from GDrive URL
  list_pdfs()          — list all PDF files in a folder (with pagination)
  download_pdf()       — download a PDF to disk with sanitized filename
  sanitize_filename()  — make a Drive filename safe to write to disk
"""
import os
import re
import time
import io
import unicodedata
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Regex: matches both URL formats, capturing the folder ID
# Format A: /drive/folders/{id}
# Format B: /drive/u/0/folders/{id}
_FOLDER_URL_RE = re.compile(
    r"https://drive\.google\.com/drive(?:/u/\d+)?/folders/([^/?&#]+)"
)

# Characters not safe in filenames on Windows/Linux
_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_RETRY_DELAYS = [0.5, 1.0, 2.0]  # seconds between retries for rate-limit errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_service():
    """
    Build and return an authenticated Google Drive API v3 service.

    Reads the service account key path from GOOGLE_SERVICE_ACCOUNT_JSON env var.
    Uses drive.readonly scope only.
    """
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not key_path:
        raise EnvironmentError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable is not set"
        )
    creds = Credentials.from_service_account_file(
        key_path, scopes=[_DRIVE_SCOPE]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def extract_folder_id(url_or_id: str) -> str:
    """
    Extract the folder ID from a Google Drive folder URL.

    Accepts:
      - https://drive.google.com/drive/folders/{id}
      - https://drive.google.com/drive/u/0/folders/{id}
      - A bare folder ID (returned unchanged)

    Raises:
        ValueError: If the input looks like a URL but no folder ID can be found.
    """
    # Strip query string / fragment for matching
    url_or_id = url_or_id.strip()

    m = _FOLDER_URL_RE.match(url_or_id)
    if m:
        return m.group(1)

    # If it starts with https:// but doesn't match — it's an unexpected URL format
    if url_or_id.startswith("https://") or url_or_id.startswith("http://"):
        raise ValueError(
            f"Could not extract folder ID from URL: {url_or_id!r}. "
            "Expected format: https://drive.google.com/drive/folders/<id>"
        )

    # Assume it's already a bare folder ID
    return url_or_id


def list_pdfs(service, folder_id: str) -> list[dict]:
    """
    List all PDF files in a Drive folder (follows pagination).

    Returns:
        List of dicts with at least 'id', 'name', and optionally 'size'.
    """
    results = []
    page_token = None
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"

    while True:
        kwargs = dict(
            q=query,
            fields="nextPageToken, files(id, name, size)",
            pageSize=100,
        )
        if page_token:
            kwargs["pageToken"] = page_token

        resp = _call_with_backoff(lambda: service.files().list(**kwargs).execute())
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results


def download_pdf(
    service,
    file_id: str,
    filename: str,
    cohort_id: int,
    download_root: str | Path = "data/downloads",
) -> tuple[Path | None, dict | None]:
    """
    Download a PDF from Drive and save it to disk.

    The file is saved to: {download_root}/{cohort_id}/{sanitized_filename}
    The sanitized filename (not the raw Drive filename) should be stored in the DB.

    Returns:
        (path, None)        on success
        (None, error_dict)  on failure (does NOT raise)
    """
    safe_name = sanitize_filename(filename)
    dest_dir = Path(download_root) / str(cohort_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / safe_name

    try:
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = _call_with_backoff(downloader.next_chunk)
        dest_path.write_bytes(buf.getvalue())
        return dest_path, None
    except Exception as exc:
        return None, {"file_id": file_id, "filename": filename, "error": str(exc)}


def sanitize_filename(name: str) -> str:
    """
    Sanitize a Drive filename for safe writing to disk.

    - Takes basename only (strips path components)
    - Replaces unsafe characters with underscores
    - Replaces '..' sequences
    - Returns a non-empty fallback if the result is empty
    """
    if not name:
        return "unnamed_file"

    # Take the basename only (handles both / and \ separators)
    basename = Path(name.replace("\\", "/")).name or name

    # Remove or replace unsafe characters
    clean = _UNSAFE_CHARS_RE.sub("_", basename)

    # Remove .. sequences
    clean = clean.replace("..", "__")

    # Strip leading/trailing dots and spaces
    clean = clean.strip(". ")

    return clean or "unnamed_file"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_rate_limit_error(exc: HttpError) -> bool:
    return exc.resp.status == 429 or (
        exc.error_details
        and any(
            d.get("reason") == "rateLimitExceeded"
            for d in (exc.error_details or [])
            if isinstance(d, dict)
        )
    )


def _call_with_backoff(fn):
    """Call fn(), retrying up to 3 times on rate-limit errors with exponential backoff."""
    last_exc = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return fn()
        except HttpError as exc:
            if _is_rate_limit_error(exc):
                last_exc = exc
                continue
            raise
    raise last_exc
