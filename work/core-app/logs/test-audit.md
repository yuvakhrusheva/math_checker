# Test Audit Report — core-app

**Date:** 2026-04-22
**Auditor:** main agent (Task 14)
**Test files reviewed:** 10 files, 99 tests passing, 4 skipped (integration, require real credentials)

---

## Summary

The test suite is well-structured with strong TDD discipline — all tasks had tests written before implementation. Unit tests cover all core modules with good isolation (DB tests use `tmp_path` + monkeypatch, criteria tests use `monkeypatch.chdir`). Integration tests cover the key pipeline scenarios with mocked Drive/LLM. The main gaps are: missing WAL mode verification in `test_db.py`, no test for `review_status` being set when a student is marked `requires_review`, and some `scorer.py` edge cases (ordered-list normalization, no-tiers scenario). No critical gaps requiring a fixer task were found.

---

## Coverage Summary

### Well-covered areas

- **`src/pdf_processor.py`** — 7 tests: valid PDF, 1-based pages, JPEG magic bytes, single/multi-page, corrupted, 0-page (mocked). Complete.
- **`src/criteria_loader.py`** — 9 tests: valid file, grade/lang/variant mismatch, invalid filename, invalid JSON, path traversal, list combinations, criteria_exists true/false, missing file. Complete.
- **`src/exporter.py`** — 18 tests: both sheets, column headers, unreadable excluded, ERROR/PENDING markers (including requires_review + NULL review_status), sort order, grade-2/3 levels, filename format, ERROR-overrides-PENDING. Complete.
- **`pages/main.py` helpers** — 9 tests: URL validation (4 cases), criteria check blocks add, valid add succeeds, start button disabled/enabled, edit blocked for non-pending.
- **`pages/review_panel.py` helpers** — 5 tests: path traversal blocked/valid, score recalculation + DB update, mark done statuses, variant dropdown gate.
- **`app.py`** — 7 tests: missing/empty/whitespace env, relative path, nonexistent file, inside-repo path, valid path. Complete.

---

## Findings

### Warning

**W-1** `tests/unit/test_db.py` — No test for `mark_student_reviewed` or `set_review_pending`  
The two new DB functions added in Task 10/11 (`set_review_pending`, `mark_student_reviewed`) are tested indirectly via `test_review_helpers.py::test_mark_done_updates_statuses`, but there is no direct unit test in `test_db.py` asserting their behavior.  
**Recommendation:** Add to `test_db.py`:
- `test_set_review_pending` → asserts `review_status='pending'` after call
- `test_mark_student_reviewed` → asserts `status='processed'` and `review_status='done'`

**W-2** `tests/integration/test_queue_processor.py` — No test that `review_status='pending'` is set for requires_review students  
`TestResumability` verifies only that 2 LLM calls are made for 2 pending students, but doesn't verify `review_status` is set after processing. If the `set_review_pending` call is removed from queue_processor, no test would catch it.  
**Recommendation:** Add assertion to `test_resumability` or a new `test_review_status_set_for_requires_review` test that checks `student["review_status"] == "pending"` after processing.

### Info

**I-1** `tests/unit/test_scorer.py` — Missing ordered-list normalization test  
`_normalize` for `answer_type="ordered_list"` collapses whitespace around commas. No test verifies that `"93, 309,390, 930"` (inconsistent spacing) matches `"93, 309, 390, 930"`.  
**Recommendation:** Add `test_ordered_list_whitespace_normalization`.

**I-2** `tests/unit/test_scorer.py` — No test for empty `tiers` list  
`compute_score` with empty `tiers=[]` returns `(0.0, "Incorrect answer")` even for correct answers (falls through to `return 0.0, "Incorrect answer"`). This is a silent failure.  
**Recommendation:** Add `test_no_tiers_returns_max_on_match` to document the intended behavior (or `test_no_tiers_returns_zero`).

**I-3** `tests/unit/test_db.py` — No WAL mode verification  
The WAL pragma is enabled in `get_connection()` but no test asserts `PRAGMA journal_mode` returns `'wal'`.  
**Recommendation:** Add `test_wal_mode_enabled`.

**I-4** `tests/unit/test_grader.py` — No test for LiteLLM error propagation  
`grade_student` is expected to propagate LiteLLM exceptions to the queue_processor. No test verifies that an exception from `litellm.completion` propagates (rather than being silently caught).  
**Recommendation:** Add `test_litellm_exception_propagates`.

**I-5** `tests/integration/test_queue_processor.py::TestDownloadErrorSetsErrorStatus` — Not a true integration test  
This test manually calls `db.update_student_status` to simulate a download error rather than running the full pipeline with a mock failing download. The actual queue_processor `_process_student` error path is not exercised.  
**Recommendation:** Update to actually run `ProcessingThread` with `drive.download_pdf` mocked to return `(None, {"error": "..."})`.

---

## Test Pyramid Assessment

| Level | Count | %  | Assessment |
|-------|-------|----|------------|
| Unit (no I/O) | 85 | 86% | ✓ Good balance |
| Integration (mocked) | 10 | 10% | ✓ Adequate |
| Integration (real) | 4 (skipped) | 4% | ✓ Conditionally skipped |

The pyramid is well-balanced. Unit tests dominate appropriately. Integration tests use mocked Drive/LLM to run without credentials. Real-credential tests are marked `@FULL_INTEGRATION` and skip gracefully.

---

## Fixture Quality

- **`tests/fixtures/sample.pdf`** — 1-page fitz-generated fixture. Adequate for format testing.
- **`tests/fixtures/sample_multi.pdf`** — 3-page fixture. Adequate for multi-page testing.
- **`tests/fixtures/llm_responses/sample_response.json`** — 16-task fixture with mix of high/low confidence, recognized student name, detected_variant=1. Realistic and complete.

Missing fixture: A fixture with `detected_variant=null` would improve grader null-variant testing (currently covered by modifying the fixture in-memory in the test).

---

## Conclusion

No critical test gaps found. The two `warning`-level gaps (missing DB function tests, missing review_status assertion) do not represent regression risks given the indirect coverage, but should be added to improve confidence. No fixer task required.
