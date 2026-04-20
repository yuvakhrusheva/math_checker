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
**Decision:** Use Python `threading.Thread` for background cohort processing. Status written to SQLite; Streamlit UI polls SQLite via `st.rerun()` on a timer to refresh progress display.
**Rationale:** Supports CA-6 (non-blocking UI). Streamlit has no native async task runner; threading is the standard pattern for background work. SQLite is the shared state between thread and UI — no shared Python objects needed.
**Alternatives considered:** `subprocess` (heavier, harder to communicate status); `concurrent.futures` (same as threading but more complex); Celery (massive overkill for a local single-user tool).

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
**Decision:** Authenticate to Google Drive API using a service account JSON key file, path stored in `GOOGLE_SERVICE_ACCOUNT_JSON` env var.
**Rationale:** Simpler than OAuth2 user flow for a local operator tool. No browser redirect needed. Service account can be granted read-only access to specific folders.
**Alternatives considered:** OAuth2 user flow (requires browser redirect, token refresh complexity).

### Decision 7: Streamlit multipage via `st.navigation` / sidebar routing
**Decision:** Use Streamlit's built-in page routing (sidebar + `st.switch_page` or `st.navigation`) for 4 screens: Criteria Management, Main (Queue), Review Panel, Export.
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
    manually_corrected INTEGER NOT NULL DEFAULT 0  -- boolean
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
- `streamlit>=1.35` — UI framework, multipage routing, session state
- `litellm>=1.40` — unified vision LLM interface
- `PyMuPDF>=1.24` (import as `fitz`) — PDF to image conversion
- `google-api-python-client>=2.130` — Google Drive API v3
- `google-auth>=2.29` — service account authentication
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

- **`test_scorer.py`**: Score calculation for all tier combinations — full, partial, zero. Edge cases: empty answer with confidence=high → 0; empty answer with confidence=low → score=0 but flagged for review.
- **`test_grader.py`**: LLM response parsing with fixture JSONs. Valid response → correct task_results. Malformed JSON → student status=error. Missing `detected_variant` (null) → student status=requires_review.
- **`test_criteria_loader.py`**: Load valid criteria JSON → returns correct schema. Load invalid JSON → raises validation error. Check available combinations (grade+language) based on files in `criteria/`.
- **`test_exporter.py`**: Excel output structure — correct sheet names ("Answers", "Scores"), correct column headers, student_id present, PENDING for unreviewed, unreadable students excluded, sort order (school → class → student_id).
- **`test_db.py`**: CRUD operations — create cohort, create student, save task_results, update status, query requires_review students.

### Integration tests

Use real Google Drive test account (`TEST_GDRIVE_FOLDER_ID` env var) with a folder containing 5 sample PDFs (3 processable, 1 unreadable, 1 with bad format):
- **Full pipeline test**: add cohort → start processing → verify all 5 students in SQLite with correct statuses
- **Resumability test**: process 3/5 students, interrupt, re-run → only remaining 2 processed (no duplicate LLM calls)
- **Drive download test**: `drive.py` lists exactly the PDFs in test folder, downloads match file sizes

### E2E tests

None — replaced by manual validation run on last year's test data (≤3% deviation from manual grading). See user verification in Agent Verification Plan.

## Agent Verification Plan

**Source:** user-spec "Как проверить" section.

### Verification approach

Per-task smoke checks verify external integrations immediately after implementation. Integration tests cover the full pipeline on real Drive data. No Streamlit UI automation — UI verified manually by user.

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

None

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

### Wave 1 — Foundation (parallel)

#### Task 1: Project infrastructure
- **Description:** Set up the full project skeleton: `requirements.txt`, `config/settings.json`, `.env.example`, folder structure (`src/`, `criteria/`, `data/`, `tests/`), pre-commit hooks (gitleaks), and `pytest.ini`. This is the foundation every other task depends on.
- **Skill:** infrastructure-setup
- **Reviewers:** code-reviewer, security-auditor, infrastructure-reviewer
- **Verify-smoke:** `pip install -r requirements.txt && python -c "import streamlit, litellm, fitz, google.oauth2; print('OK')"` → OK
- **Files to modify:** `requirements.txt`, `config/settings.json`, `.env.example`, `.gitignore`, `pytest.ini`
- **Files to read:** `architecture.md`, `deployment.md`

#### Task 2: Database layer
- **Description:** Implement `src/db.py` with full SQLite schema (DDL for cohorts, students, task_results) and all CRUD functions needed by other modules. Include `init_db()` called on app startup to create tables if not exist.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from src.db import init_db; init_db(); print('DB OK')"` → DB OK
- **Files to modify:** `src/db.py`, `tests/unit/test_db.py`
- **Files to read:** `work/core-app/tech-spec.md` (Data Models section)

#### Task 3: Criteria JSON schema and sample files
- **Description:** Define the criteria JSON schema (as in tech-spec Data Models), implement `src/criteria_loader.py` with load/validate/list-available functions, and create the 4 initially-available criteria files (`grade2_ru_v1.json`, `grade2_ru_v2.json`, `grade3_ru_v1.json`, `grade3_ru_v2.json`) based on the answer key documents provided.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `src/criteria_loader.py`, `criteria/grade2_ru_v1.json`, `criteria/grade2_ru_v2.json`, `criteria/grade3_ru_v1.json`, `criteria/grade3_ru_v2.json`, `tests/unit/test_criteria_loader.py`
- **Files to read:** `work/core-app/tech-spec.md` (Criteria JSON Schema), answer key documents in Downloads

### Wave 2 — Processing pipeline (parallel, depends on Wave 1)

#### Task 4: Google Drive integration
- **Description:** Implement `src/drive.py`: authenticate with service account from `GOOGLE_SERVICE_ACCOUNT_JSON` env var, extract folder ID from a GDrive URL, list all PDF files in the folder, and download PDFs to `data/downloads/{cohort_id}/`. Include retry with exponential backoff on rate limit errors.
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

### Wave 3 — Orchestration and UI (depends on Wave 2)

#### Task 7: Queue processor with background threading
- **Description:** Implement `src/queue_processor.py`: a `ProcessingThread` class that dequeues pending cohorts from SQLite, runs the full pipeline (drive → pdf_processor → grader) for each student sequentially, and updates statuses in real time. Expose `start()`, `is_running()` functions for the UI to call.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `src/queue_processor.py`, `tests/integration/test_queue_processor.py`
- **Files to read:** `src/drive.py`, `src/pdf_processor.py`, `src/grader.py`, `src/db.py`

#### Task 8: Criteria Management UI screen
- **Description:** Build the Criteria Management Streamlit page: file uploader for criteria JSON, validation on upload (schema + filename convention), display of currently loaded criteria combinations (grade × language × variant grid), and error messages for invalid uploads. Supports CA-1, CA-2, CA-3.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-user:** open `http://localhost:8501` → navigate to Criteria Management → upload a valid JSON → appears in grid; upload invalid JSON → error shown
- **Files to modify:** `app.py`, `pages/criteria_management.py`
- **Files to read:** `src/criteria_loader.py`, `src/db.py`

#### Task 9: Main screen UI (cohort queue)
- **Description:** Build the Main Streamlit page: cohort list table with statuses, Add Cohort form (GDrive URL + metadata fields with criteria validation), Edit/Delete for pending cohorts, Start Processing button (disabled during active processing), and live progress bar updated via `st.rerun()` polling SQLite. Supports CA-4 through CA-8.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-user:** add a cohort → appears in list as Pending; click Start Processing → progress bar updates; add another cohort while processing → queues correctly
- **Files to modify:** `pages/main.py`, `app.py`
- **Files to read:** `src/db.py`, `src/queue_processor.py`, `src/criteria_loader.py`

### Wave 4 — Review and Export (depends on Wave 3)

#### Task 10: Review Panel UI
- **Description:** Build the Review Panel Streamlit page: list of students requiring review grouped by cohort, for each student show task results with `confidence=low` — display the relevant PDF page image, task number, recognized answer, current score; allow operator to edit the answer, triggering `scorer.py` to recompute score and save correction. Handle students with `detected_variant=null` via variant dropdown. Supports CA-16 through CA-19.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Verify-user:** open Review Panel → select a student → scan page shown → edit an answer → score updates immediately → mark as done
- **Files to modify:** `pages/review_panel.py`
- **Files to read:** `src/db.py`, `src/scorer.py`, `src/pdf_processor.py`

#### Task 11: Excel exporter and Export UI
- **Description:** Implement `src/exporter.py`: query SQLite for all students across all cohorts, build two DataFrames (Answers + Scores), apply PENDING marker for unreviewed students, compute performance level for grade 3, exclude unreadable students, sort by school → class → student_id, write timestamped `.xlsx` file. Add Export button and error list display to the Main screen. Supports CA-20 through CA-25.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, test-reviewer
- **Files to modify:** `src/exporter.py`, `pages/main.py`, `tests/unit/test_exporter.py`
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
