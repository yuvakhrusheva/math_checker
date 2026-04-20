---
created: 2026-04-20
status: draft
branch: dev
size: L
---

# Tech Spec: core-app

## Solution

Build a local Streamlit application that automates AI-powered grading of handwritten math worksheets. The app manages a queue of cohorts (Google Drive folders with student PDFs), processes each PDF through a vision LLM to extract answers and scores, presents uncertain cases for manual review, and exports results to Excel.

The implementation consists of five layers: UI (Streamlit screens), orchestration (background queue processor), processing pipeline (Drive download → PDF→images → LLM grading), persistence (SQLite), and export (Excel). All LLM calls go through LiteLLM for provider flexibility.

## Architecture

### What we're building/modifying

- **`app.py`** — Streamlit entry point; routes between screens, manages `st.session_state`
- **`pages/criteria_management.py`** — Criteria Management screen (file upload, validation, loaded combinations grid)
- **`pages/main.py`** — Main screen (cohort queue table, Add Cohort form, Start Processing button, progress display, error list)
- **`pages/review_panel.py`** — Review Panel screen (uncertain students, page images, answer correction)
- **`pages/export.py`** — Export screen (Export button, file download, error list for unreadable/error students)
- **`src/db.py`** — SQLite schema (DDL) + all CRUD operations for cohorts, students, task_results
- **`src/drive.py`** — Google Drive API v3: authenticate via service account, list PDFs in a folder, download to local `data/downloads/`
- **`src/pdf_processor.py`** — PyMuPDF: open PDF, convert each page to base64 JPEG image list
- **`src/grader.py`** — LiteLLM vision call with criteria-derived prompt; parse LLM JSON response; compute scores; store results
- **`src/queue_processor.py`** — Background threading orchestrator: dequeues pending cohorts, calls drive → pdf_processor → grader sequentially, updates cohort/student status in DB
- **`src/criteria_loader.py`** — Load and validate criteria JSON files from `criteria/`; determine which grade+language combinations are available
- **`src/exporter.py`** — Build two-sheet Excel file from SQLite data using pandas + openpyxl
- **`src/scorer.py`** — Pure scoring logic: given recognized answer + criteria tier rules, compute score; used by grader (online) and review panel (manual correction)
- **`criteria/*.json`** — 8 grading configuration files (grade2/3 × ru/az × v1/v2)
- **`config/settings.json`** — LLM model name + generation settings
- **`tests/`** — pytest unit and integration tests

### How it works

```
Operator adds cohort (GDrive URL + metadata)
    → criteria_loader validates grade+language combo exists
    → cohort saved to SQLite with status=pending

Operator clicks "Start Processing"
    → queue_processor starts in background thread
    → for each pending cohort:
        → drive.py: list PDFs in GDrive folder, download each
        → for each PDF:
            → pdf_processor.py: PDF → list of base64 page images
            → grader.py: build prompt from criteria JSON, call LiteLLM vision
            → parse JSON response: recognized_name, detected_variant, tasks[]
            → scorer.py: validate scores against criteria tiers
            → save task_results to SQLite
            → set student.status = processed | requires_review | unreadable | error
        → set cohort.status = done | done_with_errors

Review Panel (on demand):
    → load students where status=requires_review
    → show scan page image + task number
    → operator edits answer → scorer.py recomputes score → save correction

Export (on demand):
    → exporter.py queries SQLite, builds DataFrame
    → writes results_YYYY-MM-DD_HH-MM.xlsx (2 sheets)
```

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| SQLite connection | `db.py` (one connection per call, not pooled) | queue_processor, app.py, exporter | N (short-lived per-call) |
| LiteLLM client | `grader.py` (module-level, stateless) | grader.py only | 1 (stateless, thread-safe) |
| Google Drive service | `drive.py` (created per cohort processing) | queue_processor via drive.py | 1 per cohort run |
| Background thread | `queue_processor.py` | app.py starts it | 1 at a time |

## Decisions

### Decision 1: Background processing via `threading.Thread`
**Decision:** Use Python `threading.Thread` for background cohort processing. Status written to SQLite; Streamlit UI polls SQLite using `st.fragment(run_every=2)` on the progress display component to auto-refresh every 2 seconds without full-page reruns. SQLite opened in WAL mode (`PRAGMA journal_mode=WAL`) to allow safe concurrent reads from the UI thread while the background thread writes.
**Rationale:** Supports CA-6, CA-8 (non-blocking UI, resumable processing). Streamlit has no native async task runner; threading is the standard pattern for background work. `st.fragment(run_every=...)` is the idiomatic Streamlit 1.36+ way to auto-refresh a component. SQLite WAL mode prevents "database is locked" errors under concurrent access.
**Alternatives considered:** `subprocess` (heavier, harder to communicate status); `concurrent.futures` (same as threading but more complex); Celery (massive overkill for a local single-user tool); `streamlit-autorefresh` third-party package (extra dependency, superseded by built-in fragment API).

### Decision 2: All PDF pages sent in one LLM request per student
**Decision:** Convert all pages of one student's PDF to base64 images and send them together in a single multi-image LLM message.
**Rationale:** Supports CA-9/CA-10 (variant detection, full task set). The worksheet is one logical document; sending pages separately would require cross-page context assembly. Single-request approach lets the LLM see the full test and determine variant from the header page.
**Alternatives considered:** One LLM call per page (loses cross-page context, complicates variant detection, doubles API calls); merging pages into one image (loses resolution, complicates page_number tracking).

### Decision 3: Criteria JSON as the single source of truth for grading prompts
**Decision:** Build LLM grading prompts dynamically from the criteria JSON at runtime. The JSON includes: task description, correct answer(s), max score, and scoring tiers with human-readable conditions.
**Rationale:** Supports patterns.md "Grading criteria as JSON" pattern. Never hardcode criteria in Python. Makes it easy to add new test configurations without code changes (supports CA-1/CA-3 criteria management).
**Alternatives considered:** Hardcoded prompts per test variant (brittle, requires code change for each new criteria file).

### Decision 4: Score recomputation via `scorer.py` (no LLM re-call on manual edit)
**Decision:** A pure-Python `scorer.py` module applies criteria tier rules to any given answer string. Used by `grader.py` after LLM response and by the Review Panel after manual correction.
**Rationale:** Supports CA-18 (instant score recalculation). Eliminates LLM round-trip cost and latency on corrections. Scoring tiers (full/partial/zero) have deterministic string-matching rules definable without LLM.
**Alternatives considered:** Re-call LLM on each manual edit (slow, costly, unnecessary).

### Decision 5: `student_id` as SQLite autoincrement INTEGER PRIMARY KEY
**Decision:** `students.id` is SQLite autoincrement PK, used as `student_id` in Excel. Globally unique across all cohorts within one SQLite database.
**Rationale:** Supports CA-15. Simple, collision-free, no external coordination needed.
**Alternatives considered:** UUID (overkill for local tool); composite key (school+class+filename — too long for Excel column).

### Decision 6: Service account auth for Google Drive [TECHNICAL]
**Decision:** Authenticate to Google Drive API using a service account JSON key file, path stored in `GOOGLE_SERVICE_ACCOUNT_JSON` env var. The service account **must be granted `drive.readonly` scope only** — the code must explicitly request `['https://www.googleapis.com/auth/drive.readonly']` when constructing credentials. The key file must have OS permissions 0600 and must reside outside the repo root (e.g., `~/.config/math_checker/service_account.json`). `GOOGLE_SERVICE_ACCOUNT_JSON` holds the absolute path to this file.
**Rationale:** Simpler than OAuth2 user flow for a local operator tool. No browser redirect needed. Limiting to `drive.readonly` scope ensures the service account cannot modify or delete Drive content even if credentials are compromised.
**Alternatives considered:** OAuth2 user flow (requires browser redirect, token refresh complexity); inline JSON in env var (risks accidental logging of full key JSON).

### Decision 7: Streamlit multipage via `st.navigation` / sidebar routing [TECHNICAL]
**Decision:** Use Streamlit's built-in page routing (sidebar + `st.switch_page` or `st.navigation`) for 4 screens: Criteria Management, Main (Queue), Review Panel, Export. Requires `streamlit>=1.36` (st.navigation was introduced in 1.36.0).
**Rationale:** Native Streamlit multipage keeps the codebase simple. No custom routing needed.
**Alternatives considered:** Single-page with `st.tabs` (cramped for this many features); custom URL routing (unnecessary complexity).

## Data Models

### SQLite Schema

```sql
CREATE TABLE cohorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    gdrive_folder_url TEXT NOT NULL,
    gdrive_folder_id TEXT NOT NULL,
    school TEXT NOT NULL,
    teacher TEXT NOT NULL,
    class_number INTEGER NOT NULL,
    class_letter TEXT NOT NULL,
    in_project INTEGER NOT NULL DEFAULT 1,  -- boolean
    language TEXT NOT NULL CHECK(language IN ('ru', 'az')),
    test_date TEXT NOT NULL,
    grade INTEGER NOT NULL CHECK(grade IN (2, 3)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'done', 'done_with_errors'))
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    gdrive_file_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    recognized_name TEXT,               -- nullable, best-effort OCR
    detected_variant INTEGER,           -- 1, 2, or NULL if not recognized
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'processed',
                         'requires_review', 'unreadable', 'error')),
    review_status TEXT CHECK(review_status IN ('pending', 'done')),
    error_message TEXT,                 -- populated on status=error/unreadable
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    task_number INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    recognized_answer TEXT,
    score REAL NOT NULL DEFAULT 0,
    max_score REAL NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('low', 'high')),
    grading_notes TEXT,
    manually_corrected INTEGER NOT NULL DEFAULT 0,  -- boolean
    UNIQUE(student_id, task_number)
);
```

### Criteria JSON Schema

File naming: `criteria/grade{2|3}_{ru|az}_v{1|2}.json`

```json
{
  "grade": 3,
  "language": "ru",
  "variant": 1,
  "total_max_score": 100,
  "tasks": [
    {
      "task_number": 1,
      "description": "Order numbers ascending",
      "max_score": 4,
      "tiers": [
        {
          "score": 4,
          "label": "full",
          "condition": "All four numbers in correct ascending order"
        },
        {
          "score": 0,
          "label": "zero",
          "condition": "Any number out of order or missing"
        }
      ],
      "correct_answers": ["93, 309, 390, 930"],
      "answer_type": "ordered_list"
    },
    {
      "task_number": 3,
      "description": "Calculate expression following order of operations",
      "max_score": 4,
      "tiers": [
        { "score": 4, "label": "full", "condition": "Correct final answer: 32" },
        { "score": 2, "label": "partial", "condition": "Correct intermediate steps but arithmetic error in final sum" },
        { "score": 0, "label": "zero", "condition": "Wrong order of operations applied" }
      ],
      "correct_answers": ["32"],
      "answer_type": "numeric"
    }
  ]
}
```

### LLM Response Schema

```json
{
  "recognized_student_name": "Иванов Иван",
  "detected_variant": 1,
  "tasks": [
    {
      "task_number": 1,
      "page_number": 1,
      "recognized_answer": "93, 309, 390, 930",
      "score": 4,
      "max_score": 4,
      "confidence": "high",
      "notes": "All numbers in correct order"
    }
  ]
}
```

## Dependencies

### New packages
- `streamlit>=1.36` — UI framework, multipage routing (`st.navigation`), session state, `st.fragment`
- `litellm>=1.40` — unified vision LLM interface
- `PyMuPDF>=1.24` (import as `fitz`) — PDF to image conversion
- `google-api-python-client>=2.130` — Google Drive API v3
- `google-auth>=2.29` — service account authentication
- `google-auth-httplib2>=0.2` — HTTP transport adapter for google-auth (required by google-api-python-client)
- `pandas>=2.2` — DataFrame for Excel export
- `openpyxl>=3.1` — Excel file writing (pandas engine)
- `python-dotenv>=1.0` — load `.env` file
- `pytest>=8.0` — test runner
- `pytest-mock>=3.12` — mocking in unit tests

### Using existing (from project)
- `sqlite3` (stdlib) — database operations via `db.py`
- `threading` (stdlib) — background queue processor
- `json` (stdlib) — criteria file parsing, LLM response parsing
- `base64` (stdlib) — encoding PDF page images for LLM input
- `pathlib` (stdlib) — file path handling

## Testing Strategy

**Feature size:** L

### Unit tests

- **`test_scorer.py`**: Score calculation for all tier combinations — full, partial, zero. Edge cases: empty answer with `confidence=high` → score=0, student not flagged for review; empty answer with `confidence=low` → score=0 AND `student.status` set to `requires_review` (verify the status transition, not just the score); partial credit boundary conditions.
- **`test_grader.py`**: LLM response parsing with fixture JSONs. Valid response → correct task_results. Malformed/unparseable JSON → student status=`error` (not `unreadable`). LLM returns valid JSON with all tasks having empty `recognized_answer` fields → student status=`unreadable`. Missing `detected_variant` (null) → student status=`requires_review`.
- **`test_pdf_processor.py`**: Convert a valid sample PDF → correct page count and non-empty base64 strings. Corrupted PDF → raises `UnreadablePDFError`. Single-page PDF → list of length 1.
- **`test_criteria_loader.py`**: Load valid criteria JSON → returns correct schema. Load invalid JSON → raises validation error. Filename/content mismatch (e.g., filename says grade3 but JSON says grade2) → validation error. Path traversal in filename → rejected. Check available combinations (grade+language) based on files in `criteria/`.
- **`test_exporter.py`**: Excel output structure — correct sheet names ("Answers", "Scores"), correct column headers, student_id present, PENDING for unreviewed students, `unreadable` and `error` students excluded, sort order (school → class → student_id), grade 3 performance levels correct, grade 2 performance level column blank.
- **`test_db.py`**: CRUD operations — create cohort, create student, save task_results, update status, query requires_review students. Verify `task_results` has UNIQUE constraint on `(student_id, task_number)` to prevent duplicate saves.

### Integration tests

Use real Google Drive test account (`TEST_GDRIVE_FOLDER_ID` env var) with a folder containing 5 sample PDFs (3 processable with known expected LLM outputs, 1 returning `confidence=low` on ≥1 task, 1 unreadable):
- **Full pipeline test**: add cohort → start processing → verify all 5 students in SQLite with correct statuses (`processed`, `requires_review`, `unreadable`). Assert that the 3 processable students have task_results rows with non-null scores. Assert that the LLM was actually called (not mocked) by checking recognized_answer is populated from the real scan.
- **Resumability test**: pre-seed 3 students as `processed` with existing task_results in SQLite, then run processing on the full cohort of 5 → verify only the 2 non-processed students are sent to the LLM; verify the 3 pre-seeded rows are unchanged (no duplicate task_results rows due to UNIQUE constraint).
- **Drive download test**: `drive.py` lists exactly the PDFs in test folder, downloaded file sizes match Drive metadata.

### E2E tests

None — replaced by manual validation run on last year's test data (≤3% deviation from manual grading). See user verification in Agent Verification Plan.

## Agent Verification Plan

**Source:** user-spec "Как проверить" section.

### Verification approach

Per-task smoke checks verify external integrations immediately after implementation. Integration tests cover the full pipeline on real Drive data. No Streamlit UI automation — UI verified manually by user via the Verify-user steps in each task. The user-spec verification table mentions "Playwright / Streamlit test" for steps 1–2 — this is superseded by manual operator verification (Verify-user) as there are no Playwright tests in this project (E2E tests excluded per user-spec "Тестирование" section).

### Tools required
- `pytest` — unit and integration test runner
- `python -c` — smoke checks for library imports and Drive API connectivity
- `bash` — file existence checks, SQLite queries via `sqlite3` CLI

## Risks

| Risk | Mitigation |
|------|-----------|
| LLM token limit exceeded for PDFs with many pages | Detect page count before sending; if >10 pages, log warning and send in batches of 5 pages, merge responses |
| LLM returns structurally valid JSON but wrong schema | Validate response against expected schema in grader.py; missing required fields → student marked error, not unreadable |
| Google Drive API quota (100 req/100sec) hit during large cohort | Add 0.5s sleep between file downloads; retry with exponential backoff on 429 errors |
| Streamlit session state lost on browser refresh during processing | Processing runs in background thread independent of Streamlit session; status read from SQLite on every page load |
| Criteria JSON uploaded with wrong grade/language/variant combo | `criteria_loader.py` validates filename format AND JSON internal fields match filename; mismatch → upload rejected with clear error |

## User-Spec Deviations

### Deviation 1: `error` students excluded from Excel (same as `unreadable`)
- **User-spec says (CA-25):** Students with status `unreadable` are not included in Excel.
- **Tech-spec does:** Students with status `error` (Drive download failure, or unparseable LLM response) are also excluded from Excel and shown in the error list alongside `unreadable` students.
- **Why:** CA-13 and CA-14 describe two failure modes (unreadable PDF, Drive download failure). Both result in incomplete/absent student data that cannot be meaningfully exported. Including `error` students in Excel with all scores blank would be misleading. They appear in the error list with their `error_message` so the operator can take corrective action.
- **Status:** [PENDING USER APPROVAL]

## Acceptance Criteria

Technical acceptance criteria (supplement user-spec CA-1 through CA-28):

- [ ] `pytest tests/unit/` passes with 0 failures
- [ ] `pytest tests/integration/` passes with real Drive test folder (5 PDFs)
- [ ] SQLite schema matches DDL spec exactly (verified via `sqlite3 data/results.db .schema`)
- [ ] All criteria JSON files validate against schema on load
- [ ] `streamlit run app.py` starts without import errors
- [ ] Background thread does not block Streamlit UI (main thread responsive during processing)
- [ ] No API keys or secrets in committed files (gitleaks clean)

## Implementation Tasks

### Wave 1 — Infrastructure (solo)

#### Task 1: Project infrastructure
- **Description:** Set up the full project skeleton: `requirements.txt`, `config/settings.json`, `.env.example`, folder structure (`src/`, `criteria/`, `data/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/llm_responses/`, `pages/`), pre-commit hooks (gitleaks), and `pytest.ini`. The `.env.example` must document that `GOOGLE_SERVICE_ACCOUNT_JSON` must be an absolute path to a key file outside the repo root. The app startup (`init_db()` call in `app.py`) must validate at launch that this env var points to an absolute path and that the file exists — fail fast with a clear error if not set or invalid.
- **Skill:** infrastructure-setup
- **Reviewers:** code-reviewer, security-auditor, infrastructure-reviewer
- **Verify-smoke:** `pip install -r requirements.txt && python -c "import streamlit, litellm, fitz, google.oauth2; print('OK')"` → OK
- **Files to modify:** `requirements.txt`, `config/settings.json`, `.env.example`, `.gitignore`, `pytest.ini`, `app.py`
- **Files to read:** `work/core-app/tech-spec.md` (Architecture, Dependencies sections)

### Wave 2 — Core Services (parallel, depends on Wave 1)

#### Task 2: Database layer
- **Description:** Implement `src/db.py` with full SQLite schema (DDL for cohorts, students, task_results) and all CRUD functions needed by other modules. Include `init_db()` called on app startup (creates tables if not exist, enables WAL mode). All queries must use parameterized statements — never string-format user input into SQL.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from src.db import init_db; init_db(); print('DB OK')"` → DB OK
- **Files to modify:** `src/db.py`, `tests/unit/test_db.py`
- **Files to read:** `work/core-app/tech-spec.md` (Data Models section)

#### Task 3: Criteria JSON schema and sample files
- **Description:** Implement `src/criteria_loader.py` with load/validate/list-available functions. When loading an uploaded criteria file, validate both the filename format (`grade{2|3}_{ru|az}_v{1|2}.json`) AND that internal `grade`/`language`/`variant` fields match the filename — reject with clear error if mismatched. Ensure the resolved write path stays within the `criteria/` directory. The 4 initially-available criteria files (`grade2_ru_v1.json`, `grade2_ru_v2.json`, `grade3_ru_v1.json`, `grade3_ru_v2.json`) are to be created by the operator via the Criteria Management UI before first use — Task 3 only provides the loader/validator and schema, not the content.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/criteria_loader.py`, `criteria/grade2_ru_v1.json`, `criteria/grade2_ru_v2.json`, `criteria/grade3_ru_v1.json`, `criteria/grade3_ru_v2.json`, `tests/unit/test_criteria_loader.py`
- **Files to read:** `work/core-app/tech-spec.md` (Criteria JSON Schema)

### Wave 3 — Processing Pipeline (parallel, depends on Wave 2)

#### Task 4: Google Drive integration
- **Description:** Implement `src/drive.py`: authenticate with service account using `drive.readonly` scope only (from `GOOGLE_SERVICE_ACCOUNT_JSON` env var path), extract folder ID from a GDrive URL, list all PDF files in the folder, and download PDFs to `data/downloads/{cohort_id}/`. Drive-returned filenames must be sanitized before writing to disk to prevent path traversal. Drive download failures must be logged to `error_message` on the student record separately from LLM/grading errors (CA-14). Implement rate-limit resilience for large folder downloads.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from src.drive import get_service; svc=get_service(); print('Drive OK')"` → Drive OK (requires valid `.env`)
- **Files to modify:** `src/drive.py`, `tests/integration/test_drive.py`
- **Files to read:** `src/db.py`, `.env.example`

#### Task 5: PDF processor
- **Description:** Implement `src/pdf_processor.py`: open a PDF with PyMuPDF, render each page to JPEG at 150 DPI, return list of `(page_number, base64_string)` tuples. Handle corrupted/empty PDFs gracefully by raising a typed `UnreadablePDFError`.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-smoke:** `python -c "from src.pdf_processor import pdf_to_images; imgs=pdf_to_images('tests/fixtures/sample.pdf'); print(len(imgs), 'pages')"` → N pages
- **Files to modify:** `src/pdf_processor.py`, `tests/unit/test_pdf_processor.py`, `tests/fixtures/sample.pdf`
- **Files to read:** `work/core-app/tech-spec.md` (LLM Response Schema)

#### Task 6: LLM grader and scoring engine
- **Description:** Implement `src/scorer.py` (pure deterministic score computation from criteria tiers) and `src/grader.py` (builds multi-image LiteLLM prompt from criteria JSON, calls vision model, parses response JSON, delegates scoring to scorer.py, returns structured result). Include fixture-based unit tests for both modules.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from src.grader import build_prompt; p=build_prompt({'tasks':[]}, []); print('Prompt OK')"` → Prompt OK
- **Files to modify:** `src/grader.py`, `src/scorer.py`, `tests/unit/test_grader.py`, `tests/unit/test_scorer.py`, `tests/fixtures/llm_responses/sample_response.json`
- **Files to read:** `src/criteria_loader.py`, `config/settings.json`, `work/core-app/tech-spec.md` (LLM contract)

### Wave 4 — Orchestration and Criteria UI (parallel, depends on Wave 3)

#### Task 7: Queue processor with background threading
- **Description:** Implement `src/queue_processor.py`: a `ProcessingThread` class that dequeues pending cohorts from SQLite, runs the full pipeline (drive → pdf_processor → grader) for each student sequentially, and updates statuses in real time. Expose `start()`, `is_running()`, and `request_stop()` functions for the UI to call (the stop flag allows the integration test to simulate an interrupt without killing the process). LLM/Drive exception messages must be sanitized before writing to `students.error_message` to prevent API key or token leakage into the DB and UI.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/queue_processor.py`, `tests/integration/test_queue_processor.py`
- **Files to read:** `src/drive.py`, `src/pdf_processor.py`, `src/grader.py`, `src/db.py`

#### Task 8: Criteria Management UI screen
- **Description:** Build the Criteria Management Streamlit page: file uploader for criteria JSON, validation on upload (schema + filename convention), display of currently loaded criteria combinations (grade × language × variant grid), and error messages for invalid uploads. Supports CA-1, CA-2, CA-3. Interface text must be in English (CA-28).
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-user:** open `http://localhost:8501` → navigate to Criteria Management → upload a valid JSON → appears in grid; upload invalid JSON → error shown
- **Files to modify:** `app.py`, `pages/criteria_management.py`
- **Files to read:** `src/criteria_loader.py`, `src/db.py`

### Wave 5 — UI Screens (parallel, depends on Wave 4)

#### Task 9: Main screen UI (cohort queue)
- **Description:** Build the Main Streamlit page: cohort list table with statuses, Add Cohort form (GDrive URL + metadata fields with criteria validation), Edit/Delete for pending cohorts (only while status=pending, per CA-7), Start Processing button (disabled during active processing per CA-6b), and live progress display updated via `st.fragment(run_every=2)` polling SQLite. Interface text must be in English (CA-28). Supports CA-4 through CA-9.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-user:** add a cohort → appears in list as Pending; click Start Processing → progress bar updates; add another cohort while processing → queues correctly; try editing a processing cohort → edit blocked
- **Files to modify:** `pages/main.py`, `app.py`
- **Files to read:** `src/db.py`, `src/queue_processor.py`, `src/criteria_loader.py`

#### Task 10: Review Panel UI
- **Description:** Build the Review Panel Streamlit page: list of students requiring review grouped by cohort, for each student show task results with `confidence=low` — display the full PDF page image (re-rendered on demand from the already-downloaded PDF in `data/downloads/{cohort_id}/`), the task number, recognized answer, and current score; allow operator to edit the answer, triggering `scorer.py` to recompute score and save correction. Handle students with `detected_variant=null` via variant dropdown (CA-19). Interface text must be in English (CA-28). Supports CA-16 through CA-19.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-user:** open Review Panel → select a student → scan page shown with task number → edit an answer → score updates immediately → mark as done
- **Files to modify:** `pages/review_panel.py`
- **Files to read:** `src/db.py`, `src/scorer.py`, `src/pdf_processor.py`

#### Task 11: Excel exporter and Export UI
- **Description:** Implement `src/exporter.py`: query SQLite for all students across all cohorts, build two DataFrames (Answers + Scores), apply PENDING marker for unreviewed students, compute performance level for grade 3 (leave blank for grade 2), exclude students with status `unreadable` or `error`, sort by school → class → student_id, write timestamped `.xlsx` file. Build `pages/export.py` — a dedicated Export screen with the Export button, file download link, and error list display (showing both `unreadable` and `error` students with their `error_message`). Interface text in English (CA-28). Supports CA-20 through CA-25.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `src/exporter.py`, `pages/export.py`, `app.py`, `tests/unit/test_exporter.py`
- **Files to read:** `src/db.py`, `work/core-app/tech-spec.md` (Data Models), `work/core-app/user-spec.md` (CA-20–CA-25)

### Audit Wave

#### Task 12: Code Audit
- **Description:** Full-feature code quality audit. Read all source files in `src/`, `pages/`, `app.py`, `tests/`. Review holistically: cross-module consistency, error handling completeness, thread safety in queue_processor, shared resource usage. Write audit report to `work/core-app/logs/code-audit.md`.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 13: Security Audit
- **Description:** Full-feature security audit. Review for: secrets in code or logs, path traversal in file download/storage, GDrive URL parsing safety, SQLite injection risks, criteria JSON validation robustness. Write report to `work/core-app/logs/security-audit.md`.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 14: Test Audit
- **Description:** Full-feature test quality audit. Review all files in `tests/`. Verify: unit test coverage of scorer/grader/exporter edge cases, integration test covers resumability and error paths, fixture quality. Write report to `work/core-app/logs/test-audit.md`.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 15: Pre-deploy QA
- **Description:** Acceptance testing: run full test suite (`pytest tests/`), verify all CA-1 through CA-28 from user-spec and technical ACs from tech-spec, confirm `streamlit run app.py` starts cleanly, run gitleaks for secrets scan.
- **Skill:** pre-deploy-qa
- **Reviewers:** none
