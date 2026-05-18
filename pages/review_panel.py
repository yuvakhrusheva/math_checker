"""Review Panel — Task 10.

В этой версии:
- _render_task_image теперь умеет обрезать страницу по bbox (нормализованным
  координатам, которые grader вернул в task_results.bbox).
- Если bbox для задачи нет — показывается вся страница, как раньше (fallback).
- Обрезка делается через Pillow (PIL), который уже ставится Streamlit'ом.
"""
import base64
import io
import json
import os
from pathlib import Path

import streamlit as st
from PIL import Image

import src.db as db
import src.scorer as scorer
import src.criteria_loader as criteria_loader
import src.pdf_processor as pdf_processor


# ---------------------------------------------------------------------------
# Testable helper functions (imported by tests/unit/test_review_helpers.py)
# ---------------------------------------------------------------------------

def resolve_pdf_path(cohort_id: int, filename: str, base_dir: str = "data/downloads") -> Path:
    """Resolve PDF path and validate it stays within base_dir."""
    base = Path(os.path.realpath(base_dir))
    candidate = base / str(cohort_id) / filename
    resolved = Path(os.path.realpath(candidate))

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
    """Recalculate score for a task given the new answer and save to DB."""
    criteria = criteria_loader.load_criteria(grade, language, variant)
    tasks = criteria.get("tasks", [])
    task_criteria = next(
        (t for t in tasks if t.get("task_number") == task_number),
        {},
    )
    score, _ = scorer.compute_score(new_answer, task_criteria)
    max_score = float(task_criteria.get("max_score", 0))

    results = db.get_task_results(student_id)
    result = next((r for r in results if r["task_number"] == task_number), None)
    if result is not None:
        db.update_task_result(result["id"], new_answer, score, manually_corrected=True)

    return score, max_score


def crop_image_by_bbox(img_bytes: bytes, bbox_json: str | None) -> bytes:
    """Обрезать изображение по bbox (JSON-строка с x1/y1/x2/y2 в [0,1]).

    Если bbox None или некорректный — возвращает оригинальные байты без обрезки.
    Возвращает JPEG-байты (или исходный формат, если не получилось распарсить bbox).

    Этот хелпер вынесен из _render_task_image, чтобы его можно было тестировать
    без Streamlit.
    """
    if not bbox_json:
        return img_bytes

    try:
        bbox = json.loads(bbox_json)
        x1 = float(bbox["x1"])
        y1 = float(bbox["y1"])
        x2 = float(bbox["x2"])
        y2 = float(bbox["y2"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return img_bytes

    if x2 <= x1 or y2 <= y1:
        return img_bytes

    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size

    left = max(0, int(x1 * w))
    upper = max(0, int(y1 * h))
    right = min(w, int(x2 * w))
    lower = min(h, int(y2 * h))

    if right - left < 10 or lower - upper < 10:
        # Слишком мелкий bbox — лучше показать страницу целиком
        return img_bytes

    cropped = img.crop((left, upper, right, lower))
    out = io.BytesIO()
    cropped.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Page rendering helpers
# ---------------------------------------------------------------------------

def _render_task_image(pdf_path: Path, page_number: int, bbox: str | None = None) -> None:
    """Render the PDF page image for a given page number, cropped by bbox if given."""
    if not pdf_path.exists():
        st.warning(f"PDF file not found on disk: {pdf_path.name}")
        return
    try:
        pages = pdf_processor.pdf_to_images(str(pdf_path))
        page_entry = next((p for p in pages if p[0] == page_number), None)
        if page_entry is None:
            st.warning(f"Page {page_number} not found in PDF.")
            return
        img_bytes = base64.b64decode(page_entry[1])
        cropped = crop_image_by_bbox(img_bytes, bbox)
        caption = (
            f"Page {page_number} — task region"
            if bbox and cropped is not img_bytes
            else f"Page {page_number}"
        )
        st.image(cropped, caption=caption)
    except pdf_processor.UnreadablePDFError as exc:
        st.warning(f"Cannot read PDF: {exc}")


def _render_student(student, cohort) -> None:
    """Render review UI for a single student."""
    student = dict(student)
    cohort = dict(cohort)
    label = (
        f"Student {student['id']}"
        + (f" — {student['recognized_name']}" if student.get("recognized_name") else "")
    )
    with st.expander(label):
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

        try:
            pdf_path = resolve_pdf_path(
                cohort_id=cohort["id"],
                filename=student["filename"],
            )
        except ValueError:
            pdf_path = None
            st.warning("Invalid PDF path — skipping image rendering.")

        task_results = db.get_task_results(student["id"])
        low_conf_results = [r for r in task_results if r["confidence"] == "low"]

        if not low_conf_results:
            st.info("No low-confidence tasks for this student.")
        else:
            for result in low_conf_results:
                result_d = dict(result)  # чтобы можно было .get() для bbox
                st.write(f"**Task {result_d['task_number']}** (Page {result_d['page_number']})")
                if pdf_path:
                    _render_task_image(
                        pdf_path,
                        result_d["page_number"],
                        bbox=result_d.get("bbox"),
                    )

                new_answer = st.text_input(
                    f"Answer for Task {result_d['task_number']}",
                    value=result_d["recognized_answer"] or "",
                    key=f"answer_{student['id']}_{result_d['task_number']}",
                )
                if new_answer != (result_d["recognized_answer"] or ""):
                    score, max_score = apply_score_update(
                        student_id=student["id"],
                        task_number=result_d["task_number"],
                        new_answer=new_answer,
                        grade=grade,
                        language=language,
                        variant=detected_variant,
                    )
                    st.write(f"Score: {score} / {max_score} ✏️")
                else:
                    st.write(f"Score: {result_d['score']} / {result_d['max_score']}")

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
