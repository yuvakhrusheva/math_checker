# Code Research: core-app

**Date:** 2026-04-20 (deepened from 2026-04-13 draft)
**Feature:** complete math_checker application (Streamlit + Google Drive + LiteLLM + SQLite + Excel export)

---

## What Code Already Exists

**Nothing.** The repository is essentially empty aside from scaffolding:

- `.env.example` — exists but is empty (0 bytes, no variables filled in)
- `README.md` — template placeholder, no project-specific content
- `CLAUDE.md` — project metadata file, filled
- `work/core-app/` — user-spec.md approved; tech-spec not yet written
- No `src/`, no `app.py`, no `requirements.txt`, no `criteria/`, no `config/`, no `tests/`

---

## What Needs to Be Built from Scratch

Everything. Full list based on architecture.md:

### Application files
| File | Purpose |
|------|---------|
| `app.py` | Streamlit entry point — all UI screens and flow |
| `src/drive.py` | Google Drive folder traversal and PDF download |
| `src/pdf_processor.py` | PDF → page images via PyMuPDF |
| `src/grader.py` | LLM calls via LiteLLM, applies grading criteria |
| `src/db.py` | SQLite operations (cohorts, students, task_results) |
| `src/exporter.py` | Excel generation (two-sheet: Answers + Scores) |

### Configuration and criteria
| File | Purpose |
|------|---------|
| `config/settings.json` | LLM model name + settings |
| `.env.example` | API key vars (currently empty) |
| `criteria/grade2_ru_v1.json` through `grade3_az_v2.json` | 8 criteria files |

### Infrastructure
| File | Purpose |
|------|---------|
| `requirements.txt` | All pip dependencies |
| `data/` directory | gitignored working dir for downloads + results.db |
| `tests/test_grader.py` | Unit tests for scoring logic |
| `tests/test_exporter.py` | Unit tests for Excel structure |
| `tests/fixtures/llm_responses/` | Saved LLM response JSON for offline testing |

---

## 1. Python Packages — Exact pip Names

```
# requirements.txt
streamlit>=1.35.0
litellm>=1.40.0
PyMuPDF>=1.24.0
google-api-python-client>=2.130.0
google-auth>=2.29.0
pandas>=2.2.0
openpyxl>=3.1.2
python-dotenv>=1.0.0
pytest>=8.2.0
```

**Package name notes (common gotchas):**
- PyMuPDF installs as `import fitz` — the pip name is `PyMuPDF`, not `fitz`
- Google Drive needs both `google-api-python-client` AND `google-auth` — the auth library is separate
- `python-dotenv` is needed to load `.env` into `os.environ`; Streamlit does not load `.env` automatically
- `openpyxl` is the Excel engine backend for pandas `.to_excel()` — must be listed explicitly

**.env.example contents (full):**
```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
```

`GOOGLE_SERVICE_ACCOUNT_JSON` holds the full service account JSON as a single-line string (not a file path). The app parses it with `json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])`.

---

## 2. Google Drive API v3 — List Files in Folder

### Authentication (service account)

```python
import json, os
from google.oauth2 import service_account
from googleapiclient.discovery import build

def get_drive_service():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)
```

### Extract folder ID from URL

```python
import re

def folder_id_from_url(url: str) -> str:
    # Handles both /folders/ID and ?id=ID formats
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract folder ID from URL: {url}")
```

### List PDF files in a folder

```python
def list_pdf_files(service, folder_id: str) -> list[dict]:
    """Returns list of {id, name} dicts for all PDF files directly in folder."""
    results = []
    page_token = None
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType='application/pdf' "
        f"and trashed=false"
    )
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=100,
            pageToken=page_token
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results  # [{"id": "1abc...", "name": "student_001.pdf"}, ...]
```

### Download a file to local path

```python
import io
from googleapiclient.http import MediaIoBaseDownload

def download_file(service, file_id: str, dest_path: str) -> None:
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(dest_path, "wb")
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.close()
```

---

## 3. PyMuPDF (fitz) — PDF Pages to Base64 Images

```python
import fitz  # PyMuPDF
import base64
from typing import Generator

def pdf_pages_to_base64(pdf_path: str, dpi: int = 150) -> Generator[tuple[int, str], None, None]:
    """
    Yields (page_number, base64_png_string) for each page.
    page_number is 1-indexed.
    dpi=150 gives ~1240×1754px for A4, sufficient for handwriting recognition.
    """
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(dpi / 72, dpi / 72)  # 72 is PDF default DPI
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            png_bytes = pix.tobytes("png")         # bytes
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            yield (page_index + 1, b64)            # 1-indexed page number
    finally:
        doc.close()
```

**Key API details:**
- `fitz.open(path)` — opens PDF; also works for paths as strings
- `page.get_pixmap(matrix=mat)` — renders page at given resolution; `matrix` scales from 72 DPI base
- `pix.tobytes("png")` — returns raw PNG bytes; also accepts `"jpeg"` for smaller size
- `pix.tobytes("jpeg", jpg_quality=85)` — JPEG variant (smaller, acceptable for handwriting at 150 DPI)
- No PIL/numpy needed; PyMuPDF handles everything natively
- For 150 DPI on A4: ~300–500 KB PNG per page; ~60–120 KB JPEG
- Use JPEG for LLM input (smaller payload, no perceptible quality loss for grading)

---

## 4. LiteLLM — Vision/Image Input for Multi-Modal Models

### Basic vision call (single image)

```python
import litellm
import json

def call_llm_vision(
    model: str,
    system_prompt: str,
    user_text: str,
    images_b64: list[str],   # list of base64 PNG/JPEG strings
    max_tokens: int = 2048,
    temperature: float = 0,
) -> dict:
    """
    Sends all pages as images in one LiteLLM completion call.
    Returns parsed JSON dict from LLM response.
    Raises ValueError if response is not valid JSON.
    """
    # Build content list: interleave text + images
    content = []
    for b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}"
            }
        })
    content.append({"type": "text", "text": user_text})

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )

    raw_text = response.choices[0].message.content
    # Strip markdown code fences if present
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0]

    return json.loads(raw_text)
```

### LiteLLM model name conventions

| Provider | Model string in config/settings.json |
|----------|--------------------------------------|
| Anthropic Claude | `"claude-3-5-sonnet-20241022"` |
| OpenAI GPT-4o | `"gpt-4o"` |
| Google Gemini | `"gemini/gemini-1.5-flash"` |
| Google Gemini Pro | `"gemini/gemini-1.5-pro"` |

**Key LiteLLM details:**
- `litellm.completion()` is the unified entry point — same signature for all providers
- Image format: `"image_url"` with `data:image/jpeg;base64,...` URI works for Claude, GPT-4o, and Gemini via LiteLLM
- For Anthropic via LiteLLM, the `image_url` format is automatically translated to Anthropic's native `image` block format
- Environment variables must be set before call: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY` — LiteLLM reads them automatically from `os.environ`
- `litellm.set_verbose = True` enables request/response logging for debugging

### Loading model config

```python
import json

def load_llm_config(path: str = "config/settings.json") -> dict:
    with open(path) as f:
        return json.load(f)
# Returns: {"model": "claude-3-5-sonnet-20241022", "max_tokens": 2048, "temperature": 0}
```

---

## 5. SQLite Schema DDL

```sql
-- cohorts table
CREATE TABLE IF NOT EXISTS cohorts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    gdrive_folder_url TEXT NOT NULL,
    gdrive_folder_id  TEXT NOT NULL,
    school          TEXT NOT NULL,
    teacher         TEXT NOT NULL,
    class_number    INTEGER NOT NULL,
    class_letter    TEXT NOT NULL,
    in_project      INTEGER NOT NULL DEFAULT 1,  -- boolean: 1=true
    language        TEXT NOT NULL CHECK(language IN ('ru', 'az')),
    test_date       TEXT NOT NULL,               -- ISO date: YYYY-MM-DD
    grade           INTEGER NOT NULL CHECK(grade IN (2, 3)),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'processing', 'done', 'done_with_errors'))
);

-- students table
CREATE TABLE IF NOT EXISTS students (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id        INTEGER NOT NULL REFERENCES cohorts(id),
    gdrive_file_id   TEXT NOT NULL,
    filename         TEXT NOT NULL,
    recognized_name  TEXT,                        -- nullable; handwritten OCR, reference only
    detected_variant INTEGER CHECK(detected_variant IN (1, 2)),  -- nullable until LLM returns
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN (
                         'pending', 'processing', 'processed',
                         'requires_review', 'unreadable', 'error'
                     )),
    review_status    TEXT CHECK(review_status IN ('pending', 'done')),  -- null until flagged
    error_message    TEXT,                        -- populated on status='error' or 'unreadable'
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- task_results table
CREATE TABLE IF NOT EXISTS task_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES students(id),
    task_number       INTEGER NOT NULL,
    page_number       INTEGER NOT NULL,           -- which PDF page holds this task (for Review Panel)
    recognized_answer TEXT,                       -- raw text the LLM read; nullable
    score             INTEGER NOT NULL DEFAULT 0,
    max_score         INTEGER NOT NULL,
    confidence        TEXT NOT NULL CHECK(confidence IN ('low', 'high')),
    grading_notes     TEXT,                       -- LLM's reasoning string; nullable
    manually_corrected INTEGER NOT NULL DEFAULT 0,  -- boolean: 1 if operator edited answer
    UNIQUE(student_id, task_number)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_students_cohort ON students(cohort_id);
CREATE INDEX IF NOT EXISTS idx_students_status ON students(status);
CREATE INDEX IF NOT EXISTS idx_task_results_student ON task_results(student_id);
```

**Python db.py patterns:**

```python
import sqlite3
from contextlib import contextmanager

DB_PATH = "data/results.db"

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # enables dict-like row access
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA_DDL)  # SCHEMA_DDL = the SQL above as a string

def get_student_status(student_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM students WHERE id = ?", (student_id,)
        ).fetchone()
        return row["status"] if row else None
```

---

## 6. Criteria JSON Schema

### File naming convention

```
criteria/grade{GRADE}_{LANG}_v{VARIANT}.json
# Examples:
criteria/grade2_ru_v1.json
criteria/grade3_ru_v2.json
criteria/grade3_az_v1.json
```

### Schema

```json
{
  "grade": 2,
  "language": "ru",
  "variant": 1,
  "total_tasks": 16,
  "tasks": [
    {
      "task_number": 1,
      "description": "Упорядочи числа от наименьшего к наибольшему",
      "max_score": 4,
      "scoring": [
        {
          "score": 4,
          "condition": "All four numbers correctly ordered: 93, 309, 390, 930"
        },
        {
          "score": 2,
          "condition": "Three of four numbers correctly placed"
        },
        {
          "score": 0,
          "condition": "Fewer than three numbers correctly placed, or no answer"
        }
      ],
      "answer_key": "93, 309, 390, 930",
      "notes": "Accept any separator (comma, space, dash)"
    },
    {
      "task_number": 2,
      "description": "Вычисли: 456 + 327 = ?",
      "max_score": 2,
      "scoring": [
        {
          "score": 2,
          "condition": "Answer is 783"
        },
        {
          "score": 0,
          "condition": "Any other answer or no answer"
        }
      ],
      "answer_key": "783",
      "notes": null
    }
  ]
}
```

**Schema field rules:**
- `scoring` array: always 2–3 tiers; last tier always has `score: 0`
- `answer_key`: string that the LLM receives as the correct answer reference
- `scoring[].condition`: plain text description sent verbatim in the LLM prompt
- Tasks with only full/zero scoring have 2 entries; tasks with partial credit have 3 entries
- All 16 tasks must be present; `total_tasks` is a validation hint

### Loading and validating criteria

```python
import json, os

def load_criteria(grade: int, language: str, variant: int) -> dict:
    path = f"criteria/grade{grade}_{language}_v{variant}.json"
    if not os.path.exists(path):
        raise FileNotFoundError(f"No criteria file: {path}")
    with open(path) as f:
        data = json.load(f)
    _validate_criteria(data)
    return data

def _validate_criteria(data: dict) -> None:
    required = {"grade", "language", "variant", "total_tasks", "tasks"}
    assert required.issubset(data.keys()), f"Missing keys: {required - data.keys()}"
    assert len(data["tasks"]) == data["total_tasks"], "Task count mismatch"
    for task in data["tasks"]:
        assert "task_number" in task and "max_score" in task and "scoring" in task
        scores = [t["score"] for t in task["scoring"]]
        assert 0 in scores, f"Task {task['task_number']} has no zero-score tier"

def list_available_criteria() -> list[dict]:
    """Returns [{grade, language, variant}] for all present criteria files."""
    result = []
    for fname in os.listdir("criteria"):
        if fname.endswith(".json"):
            # grade2_ru_v1.json → grade=2, lang=ru, variant=1
            parts = fname.replace(".json", "").split("_")
            result.append({
                "grade": int(parts[0].replace("grade", "")),
                "language": parts[1],
                "variant": int(parts[2].replace("v", "")),
                "filename": fname
            })
    return result

def criteria_exists(grade: int, language: str) -> bool:
    """Check if ANY variant exists for grade+language combo (used on cohort add)."""
    return any(
        c["grade"] == grade and c["language"] == language
        for c in list_available_criteria()
    )
```

---

## 7. Grader — LLM Prompt Construction

```python
def build_system_prompt() -> str:
    return (
        "You are a math test grader. Analyze the provided images of a student's handwritten "
        "math test. Return ONLY valid JSON — no markdown, no explanation. "
        "The JSON must match exactly the schema provided in the user message."
    )

def build_user_prompt(criteria: dict) -> str:
    lines = [
        f"Grade: {criteria['grade']}, Language: {criteria['language']}, Variant: {criteria['variant']}",
        "",
        "Grading criteria (apply exactly as written):",
    ]
    for task in criteria["tasks"]:
        lines.append(f"\nTask {task['task_number']}: {task['description']}")
        lines.append(f"  Correct answer reference: {task['answer_key']}")
        lines.append(f"  Max score: {task['max_score']}")
        lines.append("  Scoring:")
        for tier in task["scoring"]:
            lines.append(f"    - Score {tier['score']}: {tier['condition']}")

    lines += [
        "",
        "Return JSON with this exact structure:",
        '{',
        '  "recognized_student_name": "<name or null>",',
        '  "detected_variant": <1, 2, or null>,',
        '  "tasks": [',
        '    {',
        '      "task_number": <int>,',
        '      "page_number": <int>,',
        '      "recognized_answer": "<text or null>",',
        '      "score": <int>,',
        '      "max_score": <int>,',
        '      "confidence": "high" or "low",',
        '      "notes": "<optional reasoning or null>"',
        '    }',
        '  ]',
        '}',
        "",
        "Rules:",
        "- confidence='low' if handwriting is unclear, answer is ambiguous, or you are unsure of score",
        "- confidence='high' if handwriting is clear and answer is unambiguous",
        "- Empty/blank answer with clear handwriting = score 0, confidence='high'",
        "- Empty/blank answer with illegible area = score 0, confidence='low'",
        "- detected_variant: look for variant number printed on the form; null if not found",
    ]
    return "\n".join(lines)
```

---

## 8. Streamlit Background Threading Pattern

Streamlit reruns the entire script on every user interaction. Background processing must survive reruns.

### Pattern: threading.Thread + st.session_state flag

```python
# app.py — pattern for background processing queue

import threading
import streamlit as st
import time

def _processing_worker(cohort_ids: list[int], stop_event: threading.Event):
    """Runs in background thread. Writes progress to DB only (not session_state)."""
    from src.grader import process_cohort
    from src.db import update_cohort_status
    for cohort_id in cohort_ids:
        if stop_event.is_set():
            break
        update_cohort_status(cohort_id, "processing")
        try:
            process_cohort(cohort_id)
            update_cohort_status(cohort_id, "done")
        except Exception as e:
            update_cohort_status(cohort_id, "done_with_errors")

def start_processing():
    """Called when operator clicks 'Start Processing'."""
    if st.session_state.get("processing_active"):
        return  # guard: ignore if already running

    pending_ids = db.get_cohort_ids_by_status("pending")
    if not pending_ids:
        return

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_processing_worker,
        args=(pending_ids, stop_event),
        daemon=True  # thread dies when main process exits
    )
    st.session_state["processing_active"] = True
    st.session_state["processing_thread"] = thread
    st.session_state["processing_stop_event"] = stop_event
    thread.start()

def check_processing_done():
    """Called on every rerun to update processing_active flag."""
    thread = st.session_state.get("processing_thread")
    if thread and not thread.is_alive():
        st.session_state["processing_active"] = False
```

### Progress display with auto-rerun

```python
# In the main render loop:
def render_queue_panel():
    check_processing_done()

    cohorts = db.get_all_cohorts()
    for cohort in cohorts:
        col1, col2, col3 = st.columns([3, 1, 1])
        col1.write(f"{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}")
        col2.write(cohort["status"])
        total, done = db.get_cohort_progress(cohort["id"])
        if total > 0:
            col3.progress(done / total, text=f"{done}/{total}")

    if st.session_state.get("processing_active"):
        # Auto-refresh every 2 seconds while processing
        time.sleep(2)
        st.rerun()
```

**Key constraints and gotchas:**
- Background thread must NOT call any `st.*` functions — Streamlit is not thread-safe
- All inter-thread communication goes through SQLite (the DB is the shared state)
- `st.session_state` values survive reruns within a single browser session but are lost on browser refresh — the DB is the only durable state
- `daemon=True` ensures the thread is killed if the Streamlit process exits
- `st.rerun()` (formerly `st.experimental_rerun()` in older versions) triggers a full script rerun
- `time.sleep(2)` before `st.rerun()` prevents CPU spin; 2s polling interval is acceptable for this workflow
- The `processing_active` flag in `session_state` acts as a mutex to prevent double-start
- `stop_event` allows clean shutdown if needed (e.g., add a "Stop" button)

---

## 9. Streamlit UI Structure

### Page routing pattern (no st.navigation, single-page with tabs)

```python
# app.py skeleton
import streamlit as st
from dotenv import load_dotenv
load_dotenv()  # must be before any src imports that use os.environ

st.set_page_config(page_title="Math Checker", layout="wide")

tab_queue, tab_review, tab_criteria, tab_export = st.tabs([
    "Processing Queue", "Review Panel", "Criteria Management", "Export"
])

with tab_queue:
    render_queue_panel()

with tab_review:
    render_review_panel()

with tab_criteria:
    render_criteria_panel()

with tab_export:
    render_export_panel()
```

### Criteria upload via UI

```python
def render_criteria_panel():
    st.header("Criteria Management")

    # Show existing
    available = list_available_criteria()
    if available:
        st.write("Loaded criteria:")
        for c in available:
            st.write(f"  Grade {c['grade']} | {c['language'].upper()} | Variant {c['variant']}")
    else:
        st.info("No criteria files loaded yet.")

    # Upload new
    uploaded = st.file_uploader("Upload criteria JSON", type=["json"])
    if uploaded:
        try:
            data = json.load(uploaded)
            _validate_criteria(data)
            fname = f"grade{data['grade']}_{data['language']}_v{data['variant']}.json"
            os.makedirs("criteria", exist_ok=True)
            with open(f"criteria/{fname}", "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            st.success(f"Saved: {fname}")
            st.rerun()
        except json.JSONDecodeError:
            st.error("Invalid JSON file.")
        except AssertionError as e:
            st.error(f"Criteria validation failed: {e}")
```

---

## 10. Excel Export — pandas Structure

```python
import pandas as pd
from datetime import datetime

def export_to_excel(output_dir: str = ".") -> str:
    """Creates timestamped xlsx with two sheets. Returns file path."""
    students = db.get_all_processed_students()  # excludes 'unreadable'

    answers_rows = []
    scores_rows = []

    for s in students:
        tasks = db.get_task_results(s["id"])  # ordered by task_number
        task_map = {t["task_number"]: t for t in tasks}

        meta = {
            "school": s["school"],
            "class": f"{s['class_number']}{s['class_letter']}",
            "teacher": s["teacher"],
            "test_date": s["test_date"],
            "student_id": s["id"],
            "name": s["recognized_name"] or "",
        }

        is_pending = s["status"] == "requires_review" and s["review_status"] != "done"

        answer_row = dict(meta)
        score_row = dict(meta)
        total = 0

        for task_num in range(1, 17):
            t = task_map.get(task_num)
            if t is None or is_pending:
                answer_row[f"task_{task_num}"] = "PENDING"
                score_row[f"task_{task_num}"] = "PENDING"
            else:
                answer_row[f"task_{task_num}"] = t["recognized_answer"] or ""
                score_row[f"task_{task_num}"] = t["score"]
                total += t["score"]

        score_row["total"] = "PENDING" if is_pending else total
        score_row["performance_level"] = (
            "PENDING" if is_pending
            else _performance_level(s["grade"], total)
        )

        answers_rows.append(answer_row)
        scores_rows.append(score_row)

    df_answers = pd.DataFrame(answers_rows)
    df_scores = pd.DataFrame(scores_rows)

    # Sort: school → class → student_id
    sort_cols = ["school", "class", "student_id"]
    df_answers = df_answers.sort_values(sort_cols).reset_index(drop=True)
    df_scores = df_scores.sort_values(sort_cols).reset_index(drop=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"results_{timestamp}.xlsx"
    filepath = os.path.join(output_dir, filename)

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df_answers.to_excel(writer, sheet_name="Answers", index=False)
        df_scores.to_excel(writer, sheet_name="Scores", index=False)

    return filepath

def _performance_level(grade: int, total: int) -> str:
    if grade == 2:
        return ""   # TBD per spec
    # Grade 3: 100-point scale
    if total >= 81:
        return "Advanced"
    elif total >= 61:
        return "Basic"
    elif total >= 31:
        return "Minimal"
    else:
        return "Insufficient"
```

---

## 11. Streamlit UI Flow (from user-spec)

The app has a multi-step flow:

1. **Criteria Management tab** — operator uploads JSON criteria files; app validates + saves to `criteria/`
2. **Processing Queue tab** — operator fills cohort form (Drive URL, school, class, teacher, date, language); app checks criteria exist; cohort added as Pending
3. **Start Processing button** — operator clicks; background thread starts sequential cohort processing; progress bar updates via auto-rerun
4. **Review Panel tab** — after cohort completes, shows students with `requires_review` status; page image + task number; operator edits answer; score recalculates
5. **Export tab** — always available; generates timestamped `.xlsx`; Streamlit `st.download_button` serves the file

---

## 12. Technical Challenges and Decisions

### 1. Google Drive authentication setup
`GOOGLE_SERVICE_ACCOUNT_JSON` holds the full JSON as a string in `.env`. The service account must be granted Viewer access to the GDrive folder (shared via Google Drive UI). Scope: `drive.readonly`.

### 2. Handwritten student name OCR is unreliable
Name is informational only in Excel. Unique student key is auto-increment `student_id`. No logic depends on name.

### 3. Criteria JSON files availability
Grade 2 RU and Grade 3 RU exist. AZ variants pending. App blocks cohort addition if no criteria for the grade+language combo. Grade 2 performance level column left blank.

### 4. Unknown PDF page count
`pdf_pages_to_base64()` is a generator over all pages — no fixed count assumed.

### 5. Uncertain case detection
LLM returns `confidence: "low" | "high"`. Any `low` on any task → `requires_review`. Empty answer + `high` → `score=0`, not flagged. Empty answer + `low` → `score=0`, flagged for review.

### 6. LiteLLM vision input format
Use `data:image/jpeg;base64,...` URL format — works uniformly across Claude, GPT-4o, Gemini via LiteLLM. JPEG at 85% quality at 150 DPI is sufficient for handwriting recognition.

### 7. Resumable processing state
Before calling LLM for a student, check `students.status != 'pending'` and skip if already processed. DB is the only durable state.

### 8. Streamlit + background threading
`threading.Thread(daemon=True)` + SQLite as shared state + `st.rerun()` polling loop. Background thread never calls any `st.*` functions. `processing_active` flag in `session_state` guards against double-start. Full pattern in section 8 above.

### 9. Review Panel image display
`page_number` stored in `task_results` table. On review, re-render the specific page from the downloaded PDF (already on disk in `data/downloads/`). Use `fitz` to render that page to bytes; display with `st.image(png_bytes)`.

```python
def render_page_for_review(pdf_path: str, page_number: int) -> bytes:
    """Returns PNG bytes for a single page (1-indexed)."""
    doc = fitz.open(pdf_path)
    page = doc[page_number - 1]
    mat = fitz.Matrix(150 / 72, 150 / 72)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")

# In Streamlit:
# png_bytes = render_page_for_review(pdf_path, task["page_number"])
# st.image(png_bytes, caption=f"Task {task['task_number']} — Page {task['page_number']}")
```

### 10. Score recalculation on review
When operator corrects an answer, `grader.py` applies the scoring tiers from the criteria JSON locally (no LLM call):

```python
def score_answer(answer: str, task_criteria: dict) -> int:
    """
    Applies scoring tiers from criteria JSON to a given answer string.
    Used for manual review recalculation — no LLM involved.
    Returns integer score.
    Note: tier matching logic will be rule-based for MVP
    (exact match or operator-confirmed score selection).
    """
    # MVP: operator selects score directly from dropdown (0 / partial / max)
    # Future: auto-match answer text against scoring conditions
    pass
```

**MVP decision:** In Review Panel, after operator edits the answer text, show a score selector (dropdown: 0, partial credit values, max) rather than trying to auto-score free text. Operator selects the score; it is written to `task_results` with `manually_corrected=1`.

---

## 13. Gaps Still Open

1. **Actual criteria JSON content** — schema is defined (section 7), but the user must provide the content of grade2_ru and grade3_ru files. The app loads whatever is in `criteria/`.

2. **Drive folder structure** — architecture assumes one PDF per student directly in the folder (no subfolders). Confirmed by data flow diagram. `list_pdf_files()` only lists direct children.

3. **Max total score for grade 2** — performance levels for grade 2 are TBD. Column left blank in Excel.

4. **LLM token limits** — sending all pages of a multi-page PDF as images in one call. At 150 DPI JPEG (~80 KB/page), a 4-page PDF = ~320 KB of image data. Claude 3.5 Sonnet handles up to ~20 images per call; this is safe for any reasonable PDF length.

5. **Integration test setup** — user-spec calls for real GDrive test folder + real LLM calls in integration tests. Test credentials must be provided separately. Unit tests use fixtures from `tests/fixtures/llm_responses/`.
