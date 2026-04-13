# Code Research: core-app

**Date:** 2026-04-13
**Feature:** complete math_checker application (Streamlit + Google Drive + LiteLLM + SQLite + Excel export)

---

## What Code Already Exists

**Nothing.** The repository is essentially empty aside from scaffolding:

- `.env.example` — exists but is empty (0 bytes, no variables filled in)
- `README.md` — template placeholder, no project-specific content
- `CLAUDE.md` — project metadata file, filled
- `work/core-app/` — specs in progress (user-spec and tech-spec are templates, not yet filled with real content)
- No `src/`, no `app.py`, no `requirements.txt`, no `criteria/`, no `config/`, no `tests/`

The `work/core-app/logs/userspec/interview.yml` contains a completed Phase 1 interview (6 rounds of Q&A with the user), which is the richest source of requirements. The user-spec.md and tech-spec.md files themselves still contain template placeholder text — the interview was conducted but specs were not yet written from the interview.

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
| `src/db.py` | SQLite operations (sessions, folders, students, task_results) |
| `src/exporter.py` | Excel generation (two-sheet: Answers + Scores) |

### Configuration and criteria
| File | Purpose |
|------|---------|
| `config/settings.json` | LLM model name + settings |
| `.env.example` | API key vars (currently empty) |
| `criteria/grade2_ru_v1.json` through `grade3_az_v2.json` | 8 criteria files — **user says grade 2 criteria exist in RU only, grade 3 exists, AZ criteria "in a few days"** |

### Infrastructure
| File | Purpose |
|------|---------|
| `requirements.txt` | streamlit, litellm, PyMuPDF, google-api-python-client, google-auth, pandas, openpyxl, pytest |
| `data/` directory | gitignored working dir for downloads + results.db |
| `tests/test_grader.py` | Unit tests for scoring logic |
| `tests/test_exporter.py` | Unit tests for Excel structure |
| `tests/fixtures/llm_responses/` | Saved LLM response JSON for offline testing |

---

## Streamlit UI Flow (from interview)

The app has a multi-step flow based on the interview:

1. **Cohort form** — operator pastes Google Drive root folder link + metadata (school, teacher, class, grade, variant, language, test date)
2. **Processing queue** — sequential processing with progress bar; operator can add next cohort while current runs
3. **Notification / completion panel** — alerts when cohort is done, shows count of uncertain cases
4. **Review panel** — shows scan image with highlighted uncertain field; operator corrects answer; score auto-recalculates
5. **Export** — always-available button, exports current state to timestamped `.xlsx` file (e.g., `results_2026-04-13_14-30.xlsx`)

---

## Technical Challenges

### 1. Google Drive authentication setup
The `.env.example` is empty. The architecture specifies `GOOGLE_SERVICE_ACCOUNT_JSON` as the auth method. The actual variable name(s) need to be established and `.env.example` populated. Service account setup requires user action outside the app (creating credentials in Google Cloud Console and granting folder access).

### 2. Handwritten student name OCR is unreliable
Interview explicitly acknowledged this: name is written by a child, OCR is unreliable. Name is informational only in Excel. The unique student key is auto-increment. Implementation must not depend on name extraction for any logic.

### 3. Criteria JSON files availability
- Grade 2 criteria: exist in Russian only
- Grade 3 criteria: exist in Russian (assumed) — interview says "all files for grades 2 and 3 exist but only in Russian"
- Azerbaijani criteria: "in a few days"
- Grade 2 performance levels: explicitly marked TBD in patterns.md
- **Impact:** AZ criteria files cannot be created yet. The app needs to handle missing criteria gracefully (skip or error clearly). Grade 2 "performance level" column in Excel should show blank/"TBD".

### 4. Unknown PDF page count
Interview states the printed form doesn't exist yet, so page count per student PDF is unknown. The PDF processor and grader must be flexible — process all pages found, not assume a fixed count.

### 5. Uncertain case detection threshold
The app must flag cases for manual review. From interview: "illegible text → send to operator review". Empty field = score 0 (not flagged). Unrecognizable text = flagged. The LLM output contract needs a `confidence` or `uncertain` flag, or the grader infers uncertainty from the notes/recognized_answer field. This logic needs to be defined during tech-spec.

### 6. LiteLLM vision input format
Different providers handle base64 image input differently. LiteLLM abstracts this but the image encoding format in the prompt must be tested against the configured model. PyMuPDF produces PIL/numpy images that need base64 encoding for the LLM call.

### 7. Resumable processing state
SQLite tracks `students.status` (pending/processed/error). On restart, app must detect existing DB and resume from where it left off. The Streamlit session state is ephemeral — the DB is the only persistence layer.

### 8. Streamlit + background processing
Streamlit's single-threaded model makes background queue processing non-trivial. Options: `st.empty()` + polling loop, `threading`, or `st.experimental_rerun`. This is a known Streamlit limitation and needs a deliberate choice in the tech-spec.

---

## Gaps and Questions for Implementation

1. **Criteria JSON schema** — what is the exact structure? The architecture describes it abstractly (answer key, max score, scoring tiers, conditions). A concrete schema must be defined before `grader.py` can be written. Who provides the first example file?

2. **LLM output for uncertain cases** — should the JSON contract include an `uncertain: bool` field, or should the grader infer uncertainty from `recognized_answer` being empty/garbled? Needs decision.

3. **Review panel: which page to show?** — when a student has multiple pages, which page/task combination triggered the flag? The DB needs to store page number and bounding box (or at least page number) for the review panel image display.

4. **Variant detection by LLM** — architecture says variant is printed on the form and AI can detect it, but patterns.md says "grade and variant must be confirmed by operator." Interview Q4 confirms operator sets grade+variant in metadata. But interview Q3 says "AI can detect language." Clarify: does LLM auto-detect language and operator confirms grade+variant? Or does operator set all three?

5. **`.env.example` variables** — needs to be populated. At minimum: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`. The deployment.md lists these but the file is empty.

6. **User-spec and tech-spec are templates** — the interview YML has all the information needed to write both specs, but neither has been written yet. The specs need to be created before task decomposition.

7. **`criteria/` JSON files** — actual files need to be provided by the user. The app can be built to load them, but the files themselves are external input. Grade 2 RU + Grade 3 RU are reportedly ready; AZ variants pending.

---

## Summary

This is a greenfield build — zero application code exists. The project knowledge (architecture, patterns, deployment, data model) is well-documented in `.claude/skills/project-knowledge/`. The interview captured solid requirements. The main pre-implementation gap is that user-spec.md and tech-spec.md still need to be written from the interview, and the criteria JSON schema needs to be defined. Once those exist, task decomposition can proceed.
