"""Google Drive integration for math_checker (v3).

Изменения по сравнению с v2 (fixes7):
- Все list/get запросы теперь передают supportsAllDrives=True,
  includeItemsFromAllDrives=True и corpora='allDrives'. Без этих
  параметров Drive API игнорирует содержимое Shared Drives — отсюда
  «Created 0 cohorts, 0 students» при попытке импорта расшаренной
  корневой папки (она лежит в чьём-то Shared Drive или Доступных мне).
- Добавлено следование за ярлыками (mimeType=application/vnd.google-apps.shortcut):
  если в иерархии лежит shortcut на папку — он раскрывается в реальный
  folder_id через shortcutDetails.targetId. На контент это работает
  прозрачно для walker.
"""
import os
import re
import time
import io
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

_FOLDER_URL_RE = re.compile(
    r"https://drive\.google\.com/drive(?:/u/\d+)?/folders/([^/?&#]+)"
)

_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_RETRY_DELAYS = [0.5, 1.0, 2.0]

_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Общие параметры для всех list-запросов, чтобы видеть Shared Drives.
_SHARED_DRIVE_KWARGS = dict(
    supportsAllDrives=True,
    includeItemsFromAllDrives=True,
    corpora="allDrives",
)


def get_service():
    """Authenticated Google Drive v3 service."""
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
    url_or_id = url_or_id.strip()
    m = _FOLDER_URL_RE.match(url_or_id)
    if m:
        return m.group(1)
    if url_or_id.startswith("https://") or url_or_id.startswith("http://"):
        raise ValueError(
            f"Could not extract folder ID from URL: {url_or_id!r}. "
            "Expected format: https://drive.google.com/drive/folders/<id>"
        )
    return url_or_id


def list_pdfs(service, folder_id: str) -> list[dict]:
    """List all PDFs in a folder, transparently following shortcut-to-PDF entries."""
    folder_id = _resolve_shortcut(service, folder_id, expected_mime=_FOLDER_MIME) or folder_id
    raw = _list_by_query(
        service,
        f"'{folder_id}' in parents and trashed=false",
        fields="nextPageToken, files(id, name, size, mimeType, shortcutDetails)",
    )
    out = []
    for f in raw:
        mime = f.get("mimeType", "")
        if mime == "application/pdf":
            out.append({"id": f["id"], "name": f["name"], "size": f.get("size")})
        elif mime == _SHORTCUT_MIME:
            target = f.get("shortcutDetails") or {}
            if target.get("targetMimeType") == "application/pdf" and target.get("targetId"):
                out.append({"id": target["targetId"], "name": f["name"], "size": None})
    return out


def list_subfolders(service, folder_id: str) -> list[dict]:
    """List all subfolders in a folder, following folder-shortcuts."""
    folder_id = _resolve_shortcut(service, folder_id, expected_mime=_FOLDER_MIME) or folder_id
    raw = _list_by_query(
        service,
        f"'{folder_id}' in parents and trashed=false",
        fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
    )
    out = []
    for f in raw:
        mime = f.get("mimeType", "")
        if mime == _FOLDER_MIME:
            out.append({"id": f["id"], "name": f["name"]})
        elif mime == _SHORTCUT_MIME:
            target = f.get("shortcutDetails") or {}
            if target.get("targetMimeType") == _FOLDER_MIME and target.get("targetId"):
                out.append({"id": target["targetId"], "name": f["name"]})
    return out


def _resolve_shortcut(service, file_id: str, expected_mime: str) -> str | None:
    """Если file_id — это ярлык, вернуть targetId; иначе None."""
    try:
        meta = _call_with_backoff(lambda: service.files().get(
            fileId=file_id,
            fields="id, mimeType, shortcutDetails",
            supportsAllDrives=True,
        ).execute())
    except Exception:
        return None
    if meta.get("mimeType") == _SHORTCUT_MIME:
        details = meta.get("shortcutDetails") or {}
        if details.get("targetMimeType") == expected_mime:
            return details.get("targetId")
    return None


def _list_by_query(service, query: str, fields: str) -> list[dict]:
    results = []
    page_token = None
    while True:
        kwargs = dict(q=query, fields=fields, pageSize=100, **_SHARED_DRIVE_KWARGS)
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
    safe_name = sanitize_filename(filename)
    dest_dir = Path(download_root) / str(cohort_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / safe_name

    try:
        request = _call_with_backoff(lambda: service.files().get_media(
            fileId=file_id, supportsAllDrives=True,
        ))
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        dest_path.write_bytes(buf.getvalue())
        return dest_path, None
    except Exception as exc:
        return None, {"file_id": file_id, "filename": filename, "error": str(exc)}


def sanitize_filename(name: str) -> str:
    if not name:
        return "unnamed_file"
    basename = Path(name.replace("\\", "/")).name or name
    clean = _UNSAFE_CHARS_RE.sub("_", basename)
    clean = clean.replace("..", "__")
    clean = clean.strip(". ")
    return clean or "unnamed_file"


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
