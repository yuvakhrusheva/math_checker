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

Operator provides Google Drive root folder link → app traverses folder tree (any depth) and identifies leaf folders containing PDF files → operator fills metadata for each folder → PDFs downloaded locally → each PDF page converted to image → image + grading prompt + criteria sent to vision LLM → LLM returns structured JSON with recognized answers and scores → results saved to SQLite → operator triggers Excel export → two-sheet Excel file generated.

### Google Drive Folder Structure

The app expects scans to be organized as follows (depth is flexible):

```
[Root folder]
└── [Any intermediate folders — school name, date, etc.]
    └── [Leaf folder — contains PDF files]
        ├── student_001.pdf
        ├── student_002.pdf
        └── ...
```

A "leaf folder" is any folder that contains at least one PDF file. The app collects all such folders recursively from the root and presents them for metadata entry. Folder names are not parsed — all metadata is entered manually by the operator.

### LLM Input/Output Contract

**Input to LLM (per student PDF page):**
- Image of the page (base64-encoded)
- System prompt: role as math grader, instructions to return JSON only
- User prompt: task list with correct answers and scoring criteria from the criteria JSON

**Expected LLM output (JSON):**
```json
{
  "tasks": [
    {
      "task_number": 1,
      "recognized_answer": "93, 309, 390, 930",
      "score": 4,
      "max_score": 4,
      "notes": "Correct ordering"
    },
    ...
  ]
}
```

If the LLM response cannot be parsed as valid JSON, the student is marked with `status = error` and skipped.

---

## Data Model

**Database:** SQLite (`data/results.db`)

### Main Tables

**sessions**
- Purpose: One processing session = one run of the tool
- Key fields: `id`, `created_at`, `gdrive_root_url`, `status`

**folders**
- Purpose: Each Google Drive folder with scans, with operator-entered metadata
- Key fields: `id`, `session_id`, `gdrive_folder_id`, `school`, `teacher`, `class_number`, `class_letter`, `in_project` (bool), `language` (ru/az), `test_date`, `grade`, `variant`
- Relationships: `folders.session_id → sessions.id`

**students**
- Purpose: Each PDF file = one student's work
- Key fields: `id`, `folder_id`, `gdrive_file_id`, `filename`, `status` (pending/processed/error)
- Relationships: `students.folder_id → folders.id`

**task_results**
- Purpose: Recognized answer and score for each task of each student
- Key fields: `id`, `student_id`, `task_number`, `recognized_answer`, `score`, `max_score`, `grading_notes`
- Relationships: `task_results.student_id → students.id`

### Key Constraints

- **Required fields:** `folders`: school, teacher, grade, variant, language are NOT NULL
- **Status values:** `students.status` ∈ {pending, processed, error}
- **Language values:** `folders.language` ∈ {ru, az}

### Sensitive Data

- `folders.school`, `folders.teacher` — institutional data, not personal PII
- No student personal data stored (students identified by filename only)
