# Code Audit Report — core-app

**Date:** 2026-04-22
**Auditor:** main agent (Task 12)
**Files reviewed:** `src/` (6 modules), `pages/` (4 pages), `app.py`, `tests/` (10 test files)

---

## Summary

The codebase is well-structured and consistent. TDD was followed throughout, error handling covers the key pipeline failure modes (UnreadablePDFError, JSONDecodeError, Drive failures), and thread safety in `queue_processor.py` is correctly handled via `threading.Lock`. The main areas for improvement are: a documented N+1 query pattern in `exporter.py` that could cause slowness at scale, a similar N+1 in the main page render loop, and the known variant=1 default limitation in the queue processor (already documented in decisions.md). No critical issues requiring a fixer task were found.

---

## Findings

### Warning

**W-1** `src/exporter.py:76` — N+1 query pattern  
For each of N students, `db.get_task_results(sid)` is called individually. For 1,200 students this is ~1,200 SQLite queries in a single export. Given WAL mode and local SQLite, this may take 2-5 seconds but is not a correctness issue.  
**Recommendation:** Replace the per-student loop with a single `JOIN students s ON task_results t` query keyed by cohort/status filters, materializing the task-result map in Python.

**W-2** `src/queue_processor.py:133-138` — Default variant=1 for all cohorts  
The processor loads `variant=1` criteria for every student regardless of their actual test variant. Students with `detected_variant=2` are graded against the wrong criteria. This is acknowledged in decisions.md (T7) as deferred work.  
**Recommendation:** After the LLM returns `detected_variant`, reload the correct criteria and re-grade if the variant differs from 1. This is future work but important for result accuracy.

**W-3** `pages/review_panel.py:111` — `apply_score_update` triggers on every Streamlit rerun  
`criteria_loader.load_criteria` is called every time the page reruns (every keystroke), even when the answer hasn't changed. The guard `if new_answer != (result["recognized_answer"] or "")` prevents the DB write, but the criteria file is still read from disk every time.  
**Recommendation:** Cache loaded criteria in `st.session_state` per `(student_id, variant)` to avoid repeated file reads.

### Info

**I-1** `pages/main.py:76` — N+1 `list_students_by_cohort` in cohort table render  
One query per cohort row. For a local app with ≤20 cohorts, this is acceptable but worth noting.  
**Recommendation:** Add a single aggregate query `SELECT cohort_id, COUNT(*), SUM(...) FROM students GROUP BY cohort_id` to retrieve all student counts in one call.

**I-2** `src/criteria_loader.py` — No field-length validation on criteria JSON  
The validator checks structural fields (grade, language, variant) but not string lengths in `description`, `correct_answers`, or `tiers`. A crafted file with very long strings would pass validation.  
**Recommendation:** Add max-length checks for text fields (e.g., `len(task["description"]) <= 500`).

**I-3** `src/drive.py:118-150` — No file size limit on PDF downloads  
`MediaIoBaseDownload` downloads entire file into `BytesIO` with no size cap. A very large PDF (>100 MB) would consume significant memory.  
**Recommendation:** Add a file size check against the Drive API's `size` field before downloading.

**I-4** `src/grader.py:145-150` — No timeout on LiteLLM calls  
`litellm.completion` has no explicit `timeout` parameter. A stalled API call would hang the queue processor thread indefinitely.  
**Recommendation:** Add `timeout=60` (seconds) to the `litellm.completion` call and handle `TimeoutError` in the queue processor.

---

## Clean Areas

- **Error handling:** `UnreadablePDFError`, `JSONDecodeError`, and Drive `HttpError` are all caught at the right abstraction levels and mapped to correct student statuses.
- **Thread safety:** `_thread` singleton protected by `threading.Lock`; Streamlit reads DB directly (WAL mode allows concurrent reads without blocking the processor).
- **Parameterized queries:** All SQLite queries in `src/db.py` use `?` placeholders — no string formatting into SQL.
- **Criteria loading:** `load_criteria()` reads from disk each call (intentionally not cached) so new uploads are immediately visible.
- **Secret sanitization:** `sanitize_error_message()` covers 5 key patterns before any DB write.
- **Path traversal:** Both `criteria_loader.py` and `review_panel.py` use multi-layer validation.
- **Naming consistency:** `grading_notes` rename from LLM `notes` is applied uniformly in `_parse_llm_response`.
- **Test coverage:** 99 tests pass, 4 integration skipped (real credentials). TDD anchors met for all tasks.
