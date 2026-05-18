# Architecture

## Purpose
Technical architecture overview for AI agents. Helps agents understand HOW the system is built.

---

## Tech Stack

**UI Framework:** Streamlit
- **Why:** Runs locally in browser, ideal for step-by-step data processing workflows, minimal boilerplate, no separate frontend/backend needed

**Language:** Python 3.11+
- **Why:** Best ecosystem for AI/data processing; all key libraries (LiteLLM, PyMuPDF, pandas) are Python-native

**Database:** SQLite (via Python `sqlite3`)
- **Why:** Zero-config local storage for intermediate results; allows resuming processing if interrupted

---

## Project Structure

```
/
├── app.py                  # Streamlit entry point
├── config/
│   └── settings.json       # LLM model name and settings (see schema below)
├── criteria/               # Grading configs for all 8 test variants
│   ├── grade2_ru_v1.json
│   ├── grade2_ru_v2.json
│   ├── grade2_az_v1.json
│   ├── grade2_az_v2.json
│   ├── grade3_ru_v1.json
│   ├── grade3_ru_v2.json
│   ├── grade3_az_v1.json
│   └── grade3_az_v2.json
├── src/
│   ├── drive.py            # Google Drive folder traversal and PDF download
│   ├── pdf_processor.py    # PDF → page images (PyMuPDF)
│   ├── grader.py           # LLM calls via LiteLLM, applies grading criteria
│   ├── db.py               # SQLite operations (sessions, results)
│   └── exporter.py         # Excel generation (pandas + openpyxl)
├── tests/
│   ├── test_grader.py      # Unit tests for scoring logic
│   ├── test_exporter.py    # Unit tests for Excel output structure
│   └── fixtures/
│       └── llm_responses/  # Saved LLM response JSON for offline testing
├── data/                   # Local working directory (gitignored)
│   ├── downloads/          # Downloaded PDFs
│   └── results.db          # SQLite database
├── .env                    # API keys (gitignored)
├── .env.example
└── .claude/                # AI agent context
```

### config/settings.json schema

```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 2048,
  "temperature": 0
}
```

`model` follows LiteLLM naming conventions (e.g., `"gpt-4o"`, `"gemini/gemini-1.5-flash"`).

---

## Key Dependencies

- `streamlit` - Local browser UI for step-by-step operator workflow
- `litellm` - Unified interface for vision LLMs (Claude, GPT-4o, Gemini, etc.) — swap model in config without code changes
- `PyMuPDF` (fitz) - Convert PDF pages to images for LLM vision input
- `google-api-python-client` + `google-auth` - Google Drive API: folder traversal and file download
- `pandas` + `openpyxl` - Excel export with two sheets

---

## External Integrations

**Google Drive API**
- **Purpose:** Access root folder, traverse subfolder structure, download student PDF scans
- **Auth method:** OAuth2 service account credentials (`GOOGLE_SERVICE_ACCOUNT_JSON` env var) or user OAuth flow

**Vision LLM (via LiteLLM)**
- **Purpose:** Recognize handwritten answers on worksheet images and score them against criteria
- **Auth method:** Provider API key in `.env` (e.g., `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`)
- **Model config:** Set in `config/settings.json` as `"model": "claude-3-5-sonnet-20241022"` — change one line to switch providers

---

## Data Flow

Operator adds a cohort (Google Drive folder link + metadata: school, class, teacher, date, language) → app validates that criteria exist for the cohort's grade+language → cohort queued with status Pending → operator clicks "Start Processing" → app processes cohorts sequentially: downloads PDFs, converts each page to image, sends to LLM with criteria → LLM returns JSON (answers, scores, confidence, variant, student name) → results saved to SQLite → operator reviews uncertain cases in Review Panel → operator exports Excel anytime.

### Cohort Model

One cohort = one Google Drive folder + one set of metadata. The operator adds each cohort manually — there is no automatic folder traversal. Each folder contains PDF files, one per student.

```
[GDrive folder link — entered by operator]
    ├── student_001.pdf   (one PDF = one student)
    ├── student_002.pdf
    └── ...
```

### Cohort Queue & Processing Pipeline

Cohorts are processed sequentially (not in parallel) to keep Streamlit implementation simple and avoid concurrent API rate limits. Processing runs in a background thread while the UI remains responsive for adding more cohorts.

**Cohort status lifecycle:** `pending → processing → done | done_with_errors`

**Student status values:** `pending | processing | processed | requires_review | unreadable | error`
- `requires_review` — LLM returned `confidence: low` for ≥1 task, or variant not recognized
- `unreadable` — LLM could not process any page; excluded from Excel; shown in error list
- `error` — LLM response unparseable or Drive download failed

### LLM Input/Output Contract

**Input to LLM (all pages of one student's PDF combined):**
- Images of all pages (base64-encoded), sent together in one request
- System prompt: role as math grader, return JSON only
- User prompt: task list with correct answers and scoring criteria from the criteria JSON

**Expected LLM output (JSON):**
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
      "notes": "Correct ordering"
    },
    ...
  ]
}
```

- `detected_variant`: `1`, `2`, or `null` (if not recognized → student goes to review)
- `confidence`: `"low"` | `"high"` — if `low` on any task, student flagged for review
- `recognized_student_name`: best-effort OCR of handwritten name, nullable
- `page_number`: which PDF page contains this task (stored for Review Panel display)

If the LLM response cannot be parsed as valid JSON → student marked `error` and skipped.

---

## Data Model

**Database:** SQLite (`data/results.db`)

### Main Tables

**cohorts**
- Purpose: One operator-added cohort — one GDrive folder with metadata
- Key fields: `id`, `created_at`, `gdrive_folder_id`, `gdrive_folder_url`, `school`, `teacher`, `class_number`, `class_letter`, `in_project` (bool), `language` (ru/az), `test_date`, `grade`, `status` (pending/processing/done/done_with_errors)
- Note: `variant` is NOT stored here — it is detected per-student by the LLM

**students**
- Purpose: Each PDF file = one student's work
- Key fields: `id`, `cohort_id`, `gdrive_file_id`, `filename`, `recognized_name` (nullable), `detected_variant` (1/2/null), `status` (pending/processing/processed/requires_review/unreadable/error), `review_status` (pending/done, nullable)
- Relationships: `students.cohort_id → cohorts.id`

**task_results**
- Purpose: Recognized answer and score for each task of each student
- Key fields: `id`, `student_id`, `task_number`, `page_number`, `recognized_answer`, `score`, `max_score`, `confidence` (low/high), `grading_notes`, `manually_corrected` (bool)
- Relationships: `task_results.student_id → students.id`

### Key Constraints

- **Required fields:** `cohorts`: school, teacher, grade, language, gdrive_folder_url are NOT NULL
- **Student status values:** `students.status` ∈ {pending, processing, processed, requires_review, unreadable, error}
- **Confidence values:** `task_results.confidence` ∈ {low, high}
- **Language values:** `cohorts.language` ∈ {ru, az}
- **Metadata lock:** cohort metadata fields are editable only while `cohorts.status = pending`

### Sensitive Data

- `cohorts.school`, `cohorts.teacher` — institutional data, not personal PII
- `students.recognized_name` — handwritten name extracted by OCR; treated as reference data only
