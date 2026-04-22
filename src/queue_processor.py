"""Background queue processor for math_checker.

Runs a daemon thread that picks pending cohorts from SQLite, processes each
student through the Drive → PDF → LLM grading pipeline, and updates statuses
in real time.

Public API:
    start()         — start background thread (idempotent)
    is_running()    — True if thread is alive
    request_stop()  — signal thread to stop after current student finishes
"""
import json
import re
import threading
from pathlib import Path

import src.db as db
import src.drive as drive
import src.grader as grader
import src.pdf_processor as pdf_processor
import src.criteria_loader as criteria_loader

# ---------------------------------------------------------------------------
# Error message sanitization
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    re.compile(r'sk-[A-Za-z0-9]{20,}'),                          # OpenAI / Anthropic keys
    re.compile(r'AIza[A-Za-z0-9_-]{35}'),                        # Google API keys
    re.compile(r'ya29\.[A-Za-z0-9_-]+'),                         # Google OAuth tokens
    re.compile(r'Bearer [A-Za-z0-9_.\-]+'),                      # Bearer auth headers
    re.compile(r'[A-Za-z0-9+/=_\-]{40,}'),                       # Long base64-like strings
]


def sanitize_error_message(msg: str | None) -> str:
    """
    Redact API keys, tokens, and long random strings from error messages.
    Safe to call on None or empty string.
    """
    if not msg:
        return "" if msg is not None else ""
    result = str(msg)
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _determine_student_status(grading_result: dict) -> str:
    """
    Determine the final student status from grading results.

    Returns:
        'unreadable'    — all tasks empty + all high confidence
        'requires_review' — null variant OR any task has confidence=low
        'processed'     — all tasks answered with high confidence
    """
    tasks = grading_result.get("tasks", [])

    # Null variant → requires_review
    if grading_result.get("detected_variant") is None:
        return "requires_review"

    all_empty = all(not (t.get("recognized_answer") or "").strip() for t in tasks)
    all_high = all(t.get("confidence") == "high" for t in tasks)
    if tasks and all_empty and all_high:
        return "unreadable"

    # Any task has low confidence → requires_review
    if any(t.get("confidence") == "low" for t in tasks):
        return "requires_review"

    return "processed"


def _save_task_results(student_id: int, grading_result: dict) -> None:
    """Persist all task results from grading response to DB."""
    for task in grading_result.get("tasks", []):
        db.save_task_result(
            student_id=student_id,
            task_number=task["task_number"],
            page_number=task["page_number"],
            recognized_answer=task.get("recognized_answer"),
            score=float(task.get("score", 0)),
            max_score=float(task.get("max_score", 0)),
            confidence=task.get("confidence", "low"),
            grading_notes=task.get("grading_notes", ""),
        )


# ---------------------------------------------------------------------------
# ProcessingThread
# ---------------------------------------------------------------------------

class ProcessingThread(threading.Thread):
    """Daemon thread that processes all pending cohorts sequentially."""

    def __init__(self):
        super().__init__(daemon=True, name="QueueProcessorThread")
        self._stop_flag = threading.Event()

    def request_stop(self) -> None:
        """Signal the thread to stop after the current student finishes."""
        self._stop_flag.set()

    def run(self) -> None:
        service = drive.get_service()
        while not self._stop_flag.is_set():
            cohort = db.get_next_pending_cohort()
            if cohort is None:
                break
            try:
                self._process_cohort(cohort, service)
            except Exception as exc:
                # Cohort-level failure: mark done_with_errors and continue
                db.update_cohort_status(cohort["id"], "done_with_errors")

    def _process_cohort(self, cohort, service) -> None:
        cohort_id = cohort["id"]
        db.update_cohort_status(cohort_id, "processing")

        # Load criteria — start with variant 1 as default; LLM detects actual variant
        # If LLM returns detected_variant=2, criteria will be reloaded per student
        try:
            default_criteria = criteria_loader.load_criteria(
                grade=cohort["grade"],
                language=cohort["language"],
                variant=1,
            )
        except FileNotFoundError:
            db.update_cohort_status(cohort_id, "done_with_errors")
            return

        students = db.list_students_by_cohort(cohort_id)
        has_errors = False

        for student in students:
            if self._stop_flag.is_set():
                break

            # Resumability: re-fetch status and skip non-pending students
            fresh = db.get_student(student["id"])
            if fresh is None or fresh["status"] != "pending":
                continue

            error = self._process_student(fresh, service, default_criteria, cohort_id)
            if error:
                has_errors = True

        final = "done_with_errors" if has_errors else "done"
        db.update_cohort_status(cohort_id, final)

    def _process_student(self, student, service, criteria, cohort_id) -> bool:
        """
        Run the full pipeline for one student.
        Returns True if an error occurred, False on success.
        """
        student_id = student["id"]
        db.update_student_status(student_id, "processing")

        # Step 1: Download PDF
        path, error_info = drive.download_pdf(
            service,
            student["gdrive_file_id"],
            student["filename"],
            cohort_id,
        )
        if error_info is not None:
            msg = sanitize_error_message(error_info.get("error", "Download failed"))
            db.update_student_status(student_id, "error", msg)
            return True

        # Step 2: Convert PDF to images
        try:
            pages = pdf_processor.pdf_to_images(str(path))
        except pdf_processor.UnreadablePDFError as exc:
            db.update_student_status(student_id, "unreadable",
                                     sanitize_error_message(str(exc)))
            return True

        # Step 3: Grade via LLM
        try:
            result = grader.grade_student(pages, criteria)
        except json.JSONDecodeError as exc:
            db.update_student_status(student_id, "error",
                                     sanitize_error_message(f"LLM response not valid JSON: {exc}"))
            return True
        except Exception as exc:
            db.update_student_status(student_id, "error",
                                     sanitize_error_message(str(exc)))
            return True

        # Step 4: Persist recognized name + variant
        if result.get("recognized_student_name"):
            db.update_student_recognized_name(
                student_id, result["recognized_student_name"]
            )
        if result.get("detected_variant") is not None:
            db.update_student_variant(student_id, result["detected_variant"])

        # Step 5: Save task results
        _save_task_results(student_id, result)

        # Step 6: Determine and set final status
        final_status = _determine_student_status(result)
        db.update_student_status(student_id, final_status)
        return False


# ---------------------------------------------------------------------------
# Module-level singleton API
# ---------------------------------------------------------------------------

_thread: ProcessingThread | None = None
_lock = threading.Lock()


def start() -> None:
    """Start the background processing thread. No-op if already running."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = ProcessingThread()
            _thread.start()


def is_running() -> bool:
    """Return True if the processing thread is alive."""
    with _lock:
        return _thread is not None and _thread.is_alive()


def request_stop() -> None:
    """Signal the thread to stop after it finishes the current student."""
    with _lock:
        if _thread is not None:
            _thread.request_stop()
