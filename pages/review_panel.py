"""Review Panel — Task 10.

Allows operators to review students with status=requires_review.
Shows PDF page scans, lets operators edit recognized answers with live score
recalculation, and provides a "Mark as Done" button.
All UI text in English (CA-28). Supports CA-16 through CA-19.
"""
import base64
import os
from pathlib import Path

import streamlit as st

import src.db as db
import src.scorer as scorer
import src.criteria_loader as criteria_loader
import src.pdf_processor as pdf_processor


# ---------------------------------------------------------------------------
# Testable helper functions (imported by tests/unit/test_review_helpers.py)
# ---------------------------------------------------------------------------

def resolve_pdf_path(cohort_id: int, filename: str, base_dir: str = "data/downloads") -> Path:
    """
    Resolve PDF path and validate it stays within base_dir.
    Raises ValueError if the resolved path escapes the base directory (path traversal guard).
    """
    base = Path(os.path.realpath(base_dir))
    candidate = base / str(cohort_id) / filename
    resolved = Path(os.path.realpath(candidate))

    # Ensure resolved path is strictly inside base_dir
    if not (str(resolved) + os.sep).startswith(str(base) + os.sep):
        raise ValueError(
            f"Path traversal detected: {filename!r} resolves to {resolved}, "
            f"which is outside {base}"
        )
    return resolved


def should_show_task_edits(student: dict) -> bool:
    """Return True if task edit section should be shown (detected_variant is known)."""
    return student.get("detected_variant") is not None


def apply_score_update(
    student_id: int,
    task_number: int,
    new_answer: str,
    grade: int,
    language: str,
    variant: int,
) -> tuple[float, float]:
    """
    Recalculate score for a task given the new answer and save to DB (manually_corrected=1).
    Returns (score, max_score).
    """
    criteria = criteria_loader.load_criteria(grade, language, variant)
    tasks = criteria.get("tasks", [])
    task_criteria = next(
        (t for t in tasks if t.get("task_number") == task_number),
        {},
    )
    score, _ = scorer.compute_score(new_answer, task_criteria)
    max_score = float(task_criteria.get("max_score", 0))

    # Find result_id for this student + task
    results = db.get_task_results(student_id)
    result = next((r for r in results if r["task_number"] == task_number), None)
    if result is not None:
        db.update_task_result(result["id"], new_answer, score, manually_corrected=True)

    return score, max_score


# ---------------------------------------------------------------------------
# Page rendering helpers
# ---------------------------------------------------------------------------

def _render_task_image(pdf_path: Path, page_number: int) -> None:
    """Render the PDF page image for a given page number."""
    if not pdf_path.exists():
        st.warning(f"PDF file not found on disk: {pdf_path.name}")
        return
    try:
        pages = pdf_processor.pdf_to_images(str(pdf_path))
        # pages is list of (page_number_1based, base64_str)
        page_entry = next((p for p in pages if p[0] == page_number), None)
        if page_entry is None:
            st.warning(f"Page {page_number} not found in PDF.")
            return
        img_bytes = base64.b64decode(page_entry[1])
        st.image(img_bytes, caption=f"Page {page_number}")
    except pdf_processor.UnreadablePDFError as exc:
        st.warning(f"Cannot read PDF: {exc}")


def _render_student(student, cohort) -> None:
    """Render review UI for a single student."""
    # Convert sqlite3.Row to dict so .get() works uniformly
    student = dict(student)
    cohort = dict(cohort)
    label = (
        f"Student {student['id']}"
        + (f" — {student['recognized_name']}" if student.get("recognized_name") else "")
    )
    with st.expander(label):
        # Variant selector for students with unknown variant
        if not should_show_task_edits(student):
            variant = st.selectbox(
                "Select test variant",
                [1, 2],
                key=f"variant_{student['id']}",
            )
            if st.button("Apply variant", key=f"apply_variant_{student['id']}"):
                db.update_student_variant(student["id"], variant)
                st.rerun()
            st.info("Select and apply the test variant to enable answer editing.")
            return

        detected_variant = student["detected_variant"]
        grade = cohort["grade"]
        language = cohort["language"]

        # Resolve PDF path with traversal guard
        try:
            pdf_path = resolve_pdf_path(
                cohort_id=cohort["id"],
                filename=student["filename"],
            )
        except ValueError:
            pdf_path = None
            st.warning("Invalid PDF path — skipping image rendering.")

        # Show low-confidence task results
        task_results = db.get_task_results(student["id"])
        low_conf_results = [r for r in task_results if r["confidence"] == "low"]

        if not low_conf_results:
            st.info("No low-confidence tasks for this student.")
        else:
            for result in low_conf_results:
                st.write(f"**Task {result['task_number']}** (Page {result['page_number']})")
                if pdf_path:
                    _render_task_image(pdf_path, result["page_number"])

                new_answer = st.text_input(
                    f"Answer for Task {result['task_number']}",
                    value=result["recognized_answer"] or "",
                    key=f"answer_{student['id']}_{result['task_number']}",
                )
                if new_answer != (result["recognized_answer"] or ""):
                    score, max_score = apply_score_update(
                        student_id=student["id"],
                        task_number=result["task_number"],
                        new_answer=new_answer,
                        grade=grade,
                        language=language,
                        variant=detected_variant,
                    )
                    st.write(f"Score: {score} / {max_score} ✏️")
                else:
                    st.write(f"Score: {result['score']} / {result['max_score']}")

                st.divider()

        if st.button("✅ Mark as Done", key=f"done_{student['id']}"):
            db.mark_student_reviewed(student["id"])
            st.rerun()


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

st.title("Review Panel")

students_to_review = db.list_requires_review()

if not students_to_review:
    st.info("No students currently require review. All caught up!")
else:
    # Group by cohort
    cohort_ids_seen = []
    cohort_map = {}
    for student in students_to_review:
        cid = student["cohort_id"]
        if cid not in cohort_map:
            cohort_map[cid] = db.get_cohort(cid)
            cohort_ids_seen.append(cid)

    for cid in cohort_ids_seen:
        cohort = cohort_map[cid]
        st.subheader(
            f"{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}"
        )
        cohort_students = [s for s in students_to_review if s["cohort_id"] == cid]
        for student in cohort_students:
            _render_student(student, cohort)
