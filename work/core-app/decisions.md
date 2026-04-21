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
