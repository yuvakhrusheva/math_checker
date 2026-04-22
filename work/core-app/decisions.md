# Decisions Log: core-app

Agent reports on completed tasks. Each entry is written by the agent that executed the task.

---

<!-- Entries are added by agents as tasks are completed.

Format is strict — use only these sections, do not add others.
Do not include: file lists, findings tables, JSON reports, step-by-step logs.
Review details — in JSON files via links. QA report — in logs/working/.

## Task N: [title]

**Status:** Done
**Commit:** abc1234
**Agent:** [teammate name or "main agent"]
**Summary:** 1-3 sentences: what was done, key decisions. Not a file list.
**Deviations:** None / Deviated from spec: [reason], did [what].

**Reviews:**

*Round 1:*
- code-reviewer: 2 findings → [logs/working/task-N/code-reviewer-1.json]
- security-auditor: OK → [logs/working/task-N/security-auditor-1.json]

*Round 2 (after fixes):*
- code-reviewer: OK → [logs/working/task-N/code-reviewer-2.json]

**Verification:**
- `npm test` → 42 passed
- Manual check → OK

-->

## Task 1: Project infrastructure

**Status:** Done
**Commit:** 01c21e8
**Agent:** main agent
**Summary:** Created full project skeleton — requirements.txt, config/settings.json, pytest.ini (with pythonpath=.), .gitignore, .pre-commit-config.yaml (gitleaks), minimal page stubs, stub src/db.py, and app.py with validate_config() that fails fast if GOOGLE_SERVICE_ACCOUNT_JSON is missing, relative, non-existent, or inside the repo root. TDD: 7 tests written before implementation, all passing.
**Deviations:** Added 4 minimal page stubs (not in spec) so st.navigation() doesn't crash before Tasks 8–11 are implemented. Added whitespace-only and inside-repo-root edge cases beyond the 2 TDD anchors specified.

**Reviews:**

*Round 1:*
- code-reviewer: 2 critical (missing page stubs, init_db order), 5 warnings → [logs/working/task-1/code-reviewer-1.json]
- security-auditor: 2 warnings (path disclosure in UI, repo-root check missing) → [logs/working/task-1/security-auditor-1.json]
- infrastructure-reviewer: 1 critical (.gitignore `logs/` anchoring), 3 warnings → [logs/working/task-1/infrastructure-reviewer-1.json]
All findings addressed in fix commit 01c21e8.

**Verification:**
- `pytest tests/unit/test_app_startup.py -v` → 7 passed
- `python -c "import streamlit, litellm, fitz, google.oauth2; print('OK')"` → OK
- gitleaks pre-commit hook → installed and passed on commit

## Task 4: Google Drive integration

**Status:** Done
**Commit:** 60f75f3
**Agent:** main agent
**Summary:** Implemented src/drive.py with get_service, extract_folder_id, list_pdfs, download_pdf, and sanitize_filename. Supports both Drive URL formats (with/without /u/0/), pagination, rate-limit backoff (0.5s/1s/2s), and CA-14 non-raising download failures. Filename sanitization uses basename + unsafe-char replacement. After review: fixed backoff to not apply to stateful MediaIoBaseDownload.next_chunk(); added unit tests for download failure and empty folder.
**Deviations:** Integration tests (real Drive) are skipped without TEST_GDRIVE_FOLDER_ID env var — documented in test file.

**Reviews:**

*Round 1:*
- code-reviewer: 2 warnings (download_root relative path, backoff on stateful chunks) → [logs/working/task-4/code-reviewer-1.json]
- security-auditor: 2 infos → [logs/working/task-4/security-auditor-1.json]
- test-reviewer: 1 warning (missing CA-14 unit test), 1 info → [logs/working/task-4/test-reviewer-1.json]

**Verification:**
- `pytest tests/integration/test_drive.py -v` → 14 passed, 3 skipped (unit + integration-skipped)

## Task 5: PDF processor

**Status:** Done
**Commit:** 60f75f3
**Agent:** main agent
**Summary:** Implemented src/pdf_processor.py with UnreadablePDFError exception and pdf_to_images() that renders PDF pages at 150 DPI using fitz.Matrix(150/72, 150/72), encodes to JPEG base64, and returns 1-based (page_number, base64_string) tuples. Created sample.pdf (1-page) and sample_multi.pdf (3-page) fixtures via fitz. After review: added 0-page test using mock.
**Deviations:** None

**Reviews:**

*Round 1:*
- code-reviewer: OK → [logs/working/task-5/code-reviewer-1.json]
- test-reviewer: 1 info (missing 0-page test) → [logs/working/task-5/test-reviewer-1.json]

**Verification:**
- `pytest tests/unit/test_pdf_processor.py -v` → 7 passed
- `python -c "from src.pdf_processor import pdf_to_images; imgs=pdf_to_images('tests/fixtures/sample.pdf'); print(len(imgs), 'pages')"` → 1 pages

## Task 6: LLM grader and scoring engine

**Status:** Done
**Commit:** 60f75f3
**Agent:** main agent
**Summary:** Implemented scorer.py (deterministic tier-based compute_score), grader.py (build_prompt + _parse_llm_response + grade_student via LiteLLM), and sample_response.json fixture with 16 realistic Azerbaijani school tasks. LLM 'notes' field renamed to 'grading_notes' in _parse_llm_response. Confidence-based requires_review determination left to queue_processor (T7) per spec. After review: settings.json now cached at module level.
**Deviations:** None

**Reviews:**

*Round 1:*
- code-reviewer: 1 warning (settings re-read per call), 2 infos → [logs/working/task-6/code-reviewer-1.json]
- security-auditor: OK → [logs/working/task-6/security-auditor-1.json]
- test-reviewer: 1 warning (misleading partial-credit test name), 1 info → [logs/working/task-6/test-reviewer-1.json]

**Verification:**
- `pytest tests/unit/test_scorer.py -v` → 6 passed
- `pytest tests/unit/test_grader.py -v` → 7 passed
- `python -c "from src.grader import build_prompt; p=build_prompt({'tasks':[]}, []); print('Prompt OK')"` → Prompt OK

## Task 2: Database layer

**Status:** Done
**Commit:** 820e683
**Agent:** main agent
**Summary:** Implemented full SQLite database layer with 3 tables (cohorts, students, task_results), WAL mode on every connection, all-parameterized queries, and complete CRUD. `update_cohort_metadata` guards non-pending cohorts; `save_task_result` uses INSERT OR REPLACE for idempotent resumable processing. Dead `DATA_DIR` constant and duplicate WAL pragma removed after round-1 review.
**Deviations:** None

**Reviews:**

*Round 1:*
- code-reviewer: 2 warnings (dead DATA_DIR, duplicate WAL pragma) → [logs/working/task-2/code-reviewer-1.json]
- security-auditor: OK (2 infos) → [logs/working/task-2/security-auditor-1.json]
- test-reviewer: 1 warning (DATA_DIR patch no-op), 2 infos → [logs/working/task-2/test-reviewer-1.json]

*Round 2 (after fixes):*
- code-reviewer: OK → [logs/working/task-2/code-reviewer-2.json]
- test-reviewer: OK → [logs/working/task-2/test-reviewer-2.json]

**Verification:**
- `pytest tests/unit/test_db.py -v` → 7 passed
- `python -c "from src.db import init_db; init_db(); print('DB OK')"` → DB OK

## Task 3: Criteria JSON schema and loader

**Status:** Done
**Commit:** 820e683
**Agent:** main agent
**Summary:** Implemented criteria_loader.py with 5 functions: load, validate, save, list, exists. Filename regex `grade[23]_(ru|az)_v[12].json` enforced with re.fullmatch; content cross-validated against filename. Path traversal blocked by two-layer check (name separator guard + is_relative_to). Two additional tests added after review (invalid JSON, load FileNotFoundError).
**Deviations:** None

**Reviews:**

*Round 1:*
- code-reviewer: 3 infos → [logs/working/task-3/code-reviewer-1.json]
- security-auditor: OK (2 infos) → [logs/working/task-3/security-auditor-1.json]
- test-reviewer: 1 warning (missing invalid-JSON test), 2 infos → [logs/working/task-3/test-reviewer-1.json]

*Round 2 (after fixes):*
- test-reviewer: OK → [logs/working/task-3/test-reviewer-2.json]

**Verification:**
- `pytest tests/unit/test_criteria_loader.py -v` → 9 passed
