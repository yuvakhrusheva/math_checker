"""Background queue processor for math_checker — v3 (stage14).

stage14:
- При рендере PDF учитывается students.rotate_180 — если куратор отметил
  скан как «вверх ногами», страница рендерится повернутой на 180°.
- Очередь по-прежнему обрабатывает ВСЕ pending когорты (изоляция кураторов
  только на видимость в UI, а не на пайплайн обработки).
"""
import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

import src.db as db
import src.drive as drive
import src.grader as grader
import src.pdf_processor as pdf_processor
import src.criteria_loader as criteria_loader

# ---------------------------------------------------------------------------
# Error message sanitization
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    re.compile(r'sk-[A-Za-z0-9]{20,}'),
    re.compile(r'AIza[A-Za-z0-9_-]{35}'),
    re.compile(r'ya29\.[A-Za-z0-9_-]+'),
    re.compile(r'Bearer [A-Za-z0-9_.\-]+'),
    re.compile(r'[A-Za-z0-9+/=_\-]{40,}'),
]


def sanitize_error_message(msg: str | None) -> str:
    if not msg:
        return "" if msg is not None else ""
    result = str(msg)
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


# ---------------------------------------------------------------------------
# Transient-error retry helper
# ---------------------------------------------------------------------------

import time as _time

_DEFAULT_RETRY_DELAYS = (1.0, 3.0)


def _retry_call(fn, delays=_DEFAULT_RETRY_DELAYS, what: str = "operation"):
    last_exc = None
    for i, delay in enumerate([0.0] + list(delays)):
        if delay > 0:
            _time.sleep(delay)
        try:
            return fn(), None
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "%s failed on attempt %d/%d: %s",
                what, i + 1, len(delays) + 1, exc,
            )
    return None, last_exc


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _determine_student_status(grading_result: dict) -> str:
    tasks = grading_result.get("tasks", [])

    if grading_result.get("detected_variant") is None:
        return "requires_review"

    all_empty = all(not (t.get("recognized_answer") or "").strip() for t in tasks)
    all_high = all(t.get("confidence") == "high" for t in tasks)
    if tasks and all_empty and all_high:
        return "unreadable"

    if any(
        t.get("confidence") == "low"
        and (t.get("recognized_answer") or "").strip()
        for t in tasks
    ):
        return "requires_review"

    return "processed"


def _save_task_results(student_id: int, grading_result: dict) -> None:
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
            bbox=task.get("bbox"),
        )


# ---------------------------------------------------------------------------
# ProcessingThread
# ---------------------------------------------------------------------------

class ProcessingThread(threading.Thread):
    def __init__(self, started_by=None):
        super().__init__(daemon=True, name="QueueProcessorThread")
        self._stop_flag = threading.Event()
        self._started_by = started_by

    def request_stop(self) -> None:
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
                logger.exception("Cohort %s failed with unexpected error", cohort["id"])
                db.update_cohort_status(cohort["id"], "done_with_errors")

    def _process_cohort(self, cohort, service) -> None:
        cohort_id = cohort["id"]
        db.update_cohort_status(cohort_id, "processing")

        criteria_by_variant: dict[int, dict] = {}
        for v in (1, 2):
            try:
                criteria_by_variant[v] = criteria_loader.load_criteria(
                    grade=cohort["grade"],
                    language=cohort["language"],
                    variant=v,
                )
            except FileNotFoundError:
                continue

        if not criteria_by_variant:
            db.update_cohort_status(cohort_id, "done_with_errors")
            return

        students = db.list_students_by_cohort(cohort_id)
        has_errors = False

        for student in students:
            if self._stop_flag.is_set():
                break

            fresh = db.get_student(student["id"])
            if fresh is None or fresh["status"] != "pending":
                continue

            error = self._process_student(fresh, service, criteria_by_variant, cohort_id)
            if error:
                has_errors = True

        final = "done_with_errors" if has_errors else "done"
        db.update_cohort_status(cohort_id, final)

    def _process_student(self, student, service, criteria_by_variant, cohort_id) -> bool:
        student_id = student["id"]
        db.update_student_status(student_id, "processing")

        # stage14: учитываем флаг «скан повёрнут на 180°»
        rotate_180 = bool(student.get("rotate_180") or 0)

        def _do_download():
            p, err = drive.download_pdf(
                service, student["gdrive_file_id"], student["filename"], cohort_id,
            )
            if err is not None:
                raise RuntimeError(err.get("error", "Download failed"))
            return p

        path, exc = _retry_call(_do_download, what="Drive download")
        if exc is not None:
            msg = sanitize_error_message(str(exc))
            db.update_student_status(student_id, "requires_review", msg)
            try:
                db.set_review_pending(student_id)
            except AttributeError:
                pass
            return True

        try:
            pages = pdf_processor.pdf_to_images(str(path), rotate_180=rotate_180)
        except pdf_processor.UnreadablePDFError as exc:
            db.update_student_status(student_id, "requires_review",
                                     sanitize_error_message(f"Unreadable PDF: {exc}"))
            try:
                db.set_review_pending(student_id)
            except AttributeError:
                pass
            return True

        default_variant = 1 if 1 in criteria_by_variant else next(iter(criteria_by_variant))
        result, exc = _retry_call(
            lambda: grader.grade_student(pages, criteria_by_variant[default_variant]),
            what="LLM grade_student",
        )
        if exc is not None:
            if isinstance(exc, json.JSONDecodeError):
                msg = f"LLM response not valid JSON: {exc}"
            else:
                msg = str(exc)
            db.update_student_status(student_id, "requires_review",
                                     sanitize_error_message(msg))
            try:
                db.set_review_pending(student_id)
            except AttributeError:
                pass
            return True

        detected = result.get("detected_variant")
        if (detected is not None
                and detected != default_variant
                and detected in criteria_by_variant):
            try:
                result = grader.grade_student(pages, criteria_by_variant[detected])
            except json.JSONDecodeError as exc:
                db.update_student_status(student_id, "requires_review",
                                         sanitize_error_message(f"LLM response not valid JSON: {exc}"))
                try:
                    db.set_review_pending(student_id)
                except AttributeError:
                    pass
                return True
            except Exception as exc:
                db.update_student_status(student_id, "requires_review",
                                         sanitize_error_message(str(exc)))
                try:
                    db.set_review_pending(student_id)
                except AttributeError:
                    pass
                return True

        if result.get("detected_variant") is not None:
            db.update_student_variant(student_id, result["detected_variant"])

        _save_task_results(student_id, result)

        final_status = _determine_student_status(result)
        db.update_student_status(student_id, final_status)
        try:
            db.clear_student_error(student_id)
        except AttributeError:
            pass
        if final_status == "requires_review":
            db.set_review_pending(student_id)
        elif final_status == "processed" and self._started_by:
            try:
                db.set_student_reviewer(student_id, self._started_by)
            except AttributeError:
                pass
        return False


# ---------------------------------------------------------------------------
# Module-level singleton API
# ---------------------------------------------------------------------------

_thread: ProcessingThread | None = None
_lock = threading.Lock()


def start(started_by=None) -> None:
    """Start the background processing thread. No-op if already running."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = ProcessingThread(started_by=started_by)
            _thread.start()


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def request_stop() -> None:
    with _lock:
        if _thread is not None:
            _thread.request_stop()


def force_reset() -> None:
    global _thread
    with _lock:
        if _thread is not None:
            _thread.request_stop()
            if not _thread.is_alive():
                _thread = None
