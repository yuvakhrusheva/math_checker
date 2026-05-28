"""Review Panel — v5 (fixes14).

Что нового по сравнению с v3 (fixes13):
- Каскадные фильтры в шапке: Sector → Grade → School → Teacher.
  Каждый куратор может выбрать «свою» подвыборку. На каждом уровне
  доступен вариант «All» — например, школа выбрана, а учитель «All»
  означает «все учителя этой школы».
- Расширенный bbox: добавляется padding 3% по горизонтали и 4% по вертикали
  перед обрезкой. Куратор почти всегда видит шапку «№ N» и поле ответа,
  даже если LLM был не очень точен.
- Кнопка «📄 Show full page» под каждой задачей — если обрезка всё-таки
  промахнулась, куратор одним кликом раскрывает весь скан страницы.
- Счётчик отфильтрованных студентов в шапке.

Остальное (кнопки Correct/Incorrect, number_input, error_message, ФИО)
сохранено из fixes13.
"""
import base64
import io
import json
import os
from pathlib import Path

import streamlit as st
from PIL import Image

import src.db as db
import src.criteria_loader as criteria_loader
import src.pdf_processor as pdf_processor
import src.drive_walker as drive_walker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_pdf_path(cohort_id: int, filename: str, base_dir: str = "data/downloads") -> Path:
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
    return student.get("detected_variant") is not None


def is_visual_task(task_criteria: dict) -> bool:
    if task_criteria.get("correct_answers"):
        return False
    partial = [
        t for t in task_criteria.get("tiers", [])
        if t.get("label") not in ("full", "zero")
    ]
    return not partial


def score_step_for_task(task_criteria: dict) -> float:
    tier_scores = sorted({float(t.get("score", 0)) for t in task_criteria.get("tiers", [])})
    diffs = [
        tier_scores[i + 1] - tier_scores[i]
        for i in range(len(tier_scores) - 1)
        if tier_scores[i + 1] - tier_scores[i] > 0
    ]
    return min(diffs) if diffs else 1.0


def apply_manual_score(result_id: int, recognized_answer: str, new_score: float) -> None:
    db.update_task_result(
        result_id, recognized_answer or "", float(new_score),
        manually_corrected=True,
    )


def display_name_for_student(student: dict) -> str:
    name = (student.get("recognized_name") or "").strip()
    if name:
        return name
    fn = student.get("filename")
    if fn:
        try:
            parsed, _, _ = drive_walker.parse_student_filename(fn)
            if parsed:
                return parsed
        except Exception:
            pass
    return f"Student {student.get('id', '?')}"


# --- bbox crop with padding ---

# Padding в долях страницы — добавляется к bbox перед обрезкой, чтобы куратор
# гарантированно видел шапку «№ N» сверху, но НЕ захватывал кусок
# следующей задачи снизу.
_BBOX_PADDING_X = 0.03
_BBOX_PADDING_TOP = 0.06       # больше — захватывает заголовок «№ N» / «6 б.»
_BBOX_PADDING_BOTTOM = 0.01    # меньше — чтобы не вылезать на следующую задачу


def crop_image_by_bbox(img_bytes: bytes, bbox_json: str | None) -> bytes:
    """Crop image by normalized bbox (with padding). Returns original on any issue."""
    if not bbox_json:
        return img_bytes
    try:
        bbox = json.loads(bbox_json)
        x1 = float(bbox["x1"]); y1 = float(bbox["y1"])
        x2 = float(bbox["x2"]); y2 = float(bbox["y2"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return img_bytes
    if x2 <= x1 or y2 <= y1:
        return img_bytes

    # Расширяем bbox padding'ом (сверху больше, снизу почти нет).
    x1 = max(0.0, x1 - _BBOX_PADDING_X)
    y1 = max(0.0, y1 - _BBOX_PADDING_TOP)
    x2 = min(1.0, x2 + _BBOX_PADDING_X)
    y2 = min(1.0, y2 + _BBOX_PADDING_BOTTOM)

    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    left = max(0, int(x1 * w))
    upper = max(0, int(y1 * h))
    right = min(w, int(x2 * w))
    lower = min(h, int(y2 * h))
    if right - left < 10 or lower - upper < 10:
        return img_bytes
    cropped = img.crop((left, upper, right, lower))
    out = io.BytesIO()
    cropped.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def _render_task_image(pdf_path: Path, page_number: int, bbox: str | None,
                       full_page: bool = False) -> None:
    """Render the page image; if full_page=False and bbox is present, crop."""
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
        if not full_page and bbox:
            img_bytes = crop_image_by_bbox(img_bytes, bbox)
            caption = f"Task on page {page_number}"
        else:
            caption = f"Page {page_number} (full)"
        st.image(img_bytes, caption=caption)
    except pdf_processor.UnreadablePDFError as exc:
        st.warning(f"Cannot read PDF: {exc}")


def _render_low_conf_task(sid, result, task_criteria, pdf_path):
    task_num = result["task_number"]
    page_num = result["page_number"]
    recognized = result.get("recognized_answer") or ""
    current_score = float(result.get("score") or 0)
    max_score = float(task_criteria.get("max_score", result.get("max_score") or 0))
    bbox = result.get("bbox")

    st.write(f"**Task {task_num}** (Page {page_num})")

    # Кнопка-переключатель «обрезка / вся страница». Состояние хранится в
    # session_state — на каждую задачу свой ключ.
    full_key = f"fullpage_{sid}_{task_num}"
    full_page = st.session_state.get(full_key, False)
    btn_label = "🔍 Show cropped task" if full_page else "📄 Show full page"
    if pdf_path:
        _render_task_image(pdf_path, page_num, bbox=bbox, full_page=full_page)
        if bbox:
            if st.button(btn_label, key=f"toggle_{sid}_{task_num}"):
                st.session_state[full_key] = not full_page
                st.rerun()

    st.caption(f"Recognized answer: {recognized or '—'}")
    st.caption(f"Current score: {current_score} / {max_score}")

    if is_visual_task(task_criteria):
        cols = st.columns(2)
        with cols[0]:
            if st.button(
                f"✅ Correct ({max_score:g} pts)",
                key=f"correct_{sid}_{task_num}", use_container_width=True,
            ):
                apply_manual_score(result["id"], recognized, max_score)
                st.rerun()
        with cols[1]:
            if st.button(
                "❌ Incorrect (0 pts)",
                key=f"incorrect_{sid}_{task_num}", use_container_width=True,
            ):
                apply_manual_score(result["id"], recognized, 0.0)
                st.rerun()
    else:
        step = score_step_for_task(task_criteria)
        initial = max(0.0, min(current_score, max_score))
        new_score = st.number_input(
            f"Score for Task {task_num}",
            min_value=0.0, max_value=max_score, value=initial, step=step,
            key=f"score_{sid}_{task_num}",
        )
        save_col, info_col = st.columns([1, 3])
        with save_col:
            if st.button("💾 Save score", key=f"save_{sid}_{task_num}"):
                apply_manual_score(result["id"], recognized, new_score)
                st.success(f"Saved: {new_score:g} / {max_score:g}")
                st.rerun()
        with info_col:
            if new_score != current_score:
                st.caption("Press **Save score** to apply.")

    st.divider()


def _render_student(student, cohort) -> None:
    student = dict(student)
    cohort = dict(cohort)
    label = display_name_for_student(student)
    with st.expander(label):
        err = (student.get("error_message") or "").strip()
        if err:
            st.error(f"⚠️ Processing error: {err}")
            st.caption(
                "Чтобы перезапустить обработку — в Cohort Queue нажми кнопку 🔄 "
                "у этой когорты, потом ▶️ Start Processing. После успешной "
                "обработки эта пометка исчезнет автоматически."
            )

        if not should_show_task_edits(student):
            variant = st.selectbox(
                "Select test variant", [1, 2],
                key=f"variant_{student['id']}",
            )
            cols = st.columns(2)
            with cols[0]:
                if st.button("✅ Apply variant & re-grade",
                             key=f"apply_variant_{student['id']}",
                             help=(
                                 "Сохранит выбранный вариант, очистит "
                                 "предыдущие баллы и поставит ученика "
                                 "обратно в очередь — нажми Start Processing "
                                 "в Cohort Queue, чтобы ИИ прогнал работу "
                                 "заново уже с правильным вариантом."
                             )):
                    db.update_student_variant(student["id"], variant)
                    try:
                        db.reset_student_for_reprocessing(student["id"])
                    except AttributeError:
                        # Старый db без хелпера — хоть status сбросим
                        db.update_student_status(student["id"], "pending")
                    st.success(
                        f"Variant {variant} applied. Student re-queued. "
                        "Open Cohort Queue and press ▶️ Start Processing."
                    )
                    st.rerun()
            with cols[1]:
                if st.button("Just save variant (no re-grade)",
                             key=f"apply_variant_only_{student['id']}",
                             help=(
                                 "Просто сохранить вариант без переотправки на "
                                 "проверку — используй, если уже есть баллы и "
                                 "ты не хочешь их потерять."
                             )):
                    db.update_student_variant(student["id"], variant)
                    st.rerun()
            st.info(
                "Select a test variant. **Apply variant & re-grade** запустит "
                "ИИ заново — рекомендуется, иначе у работы не будет баллов."
            )
            return

        detected_variant = student["detected_variant"]
        grade = cohort["grade"]
        language = cohort["language"]

        try:
            pdf_path = resolve_pdf_path(
                cohort_id=cohort["id"], filename=student["filename"],
            )
        except ValueError:
            pdf_path = None
            st.warning("Invalid PDF path — skipping image rendering.")

        try:
            criteria = criteria_loader.load_criteria(grade, language, detected_variant)
        except FileNotFoundError:
            st.error(
                f"Criteria file missing for grade={grade}, lang={language}, "
                f"variant={detected_variant}."
            )
            return
        tasks_criteria = {t["task_number"]: t for t in criteria.get("tasks", [])}

        task_results = db.get_task_results(student["id"])
        low_conf_results = [r for r in task_results if r["confidence"] == "low"]

        if not low_conf_results:
            st.info("No low-confidence tasks for this student.")
        else:
            for result in low_conf_results:
                r = dict(result)
                tc = tasks_criteria.get(r["task_number"], {})
                _render_low_conf_task(student["id"], r, tc, pdf_path)

        if st.button("✅ Mark as Done", key=f"done_{student['id']}"):
            db.mark_student_reviewed(student["id"])
            st.rerun()


# ---------------------------------------------------------------------------
# Cascading filters
# ---------------------------------------------------------------------------

_LANG_LABEL = {"ru": "Ру сектор", "az": "Аз сектор"}


def _apply_filters(students, cohort_map):
    """Render filter widgets and return the filtered student list.

    Каскад: Sector → Grade → School → Teacher. На каждом уровне доступен
    «All». Каждое последующее меню формируется по уже отфильтрованному
    набору, чтобы не показывать пустых вариантов.
    """
    if not students:
        return students

    # All cohorts that have students requiring review.
    cohorts_in_play = [
        cohort_map[s["cohort_id"]] for s in students
    ]

    def options(field):
        return sorted({c[field] for c in cohorts_in_play if c.get(field) is not None})

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        sector_choice = st.selectbox(
            "Sector",
            ["All"] + [_LANG_LABEL.get(s, s) for s in options("language")],
            key="filter_sector",
        )
    sector_value = None
    if sector_choice != "All":
        for code, lbl in _LANG_LABEL.items():
            if lbl == sector_choice:
                sector_value = code
                break
        if sector_value is None:
            sector_value = sector_choice

    after_sector = [
        c for c in cohorts_in_play
        if sector_value is None or c["language"] == sector_value
    ]

    with col2:
        grade_options = sorted({c["grade"] for c in after_sector})
        grade_choice = st.selectbox(
            "Grade",
            ["All"] + [f"{g} класс" for g in grade_options],
            key="filter_grade",
        )
    grade_value = None
    if grade_choice != "All":
        grade_value = int(grade_choice.split()[0])

    after_grade = [
        c for c in after_sector
        if grade_value is None or c["grade"] == grade_value
    ]

    with col3:
        school_options = sorted({c["school"] for c in after_grade})
        school_choice = st.selectbox(
            "School", ["All"] + school_options, key="filter_school",
        )
    after_school = [
        c for c in after_grade
        if school_choice == "All" or c["school"] == school_choice
    ]

    with col4:
        teacher_options = sorted({c["teacher"] for c in after_school})
        teacher_choice = st.selectbox(
            "Teacher", ["All"] + teacher_options, key="filter_teacher",
        )
    after_teacher = [
        c for c in after_school
        if teacher_choice == "All" or c["teacher"] == teacher_choice
    ]

    allowed_cohort_ids = {c["id"] for c in after_teacher}
    return [s for s in students if s["cohort_id"] in allowed_cohort_ids]


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

st.title("Review Panel")

students_to_review = db.list_requires_review()

if not students_to_review:
    st.info("No students currently require review. All caught up!")
else:
    # Build cohort_map once
    cohort_map = {}
    for student in students_to_review:
        cid = student["cohort_id"]
        if cid not in cohort_map:
            cohort_map[cid] = dict(db.get_cohort(cid))

    # Filters
    st.caption("**Filters** — narrow down which students you review:")
    filtered_students = _apply_filters(students_to_review, cohort_map)

    st.write(
        f"Showing **{len(filtered_students)}** of **{len(students_to_review)}** "
        "students requiring review."
    )
    st.divider()

    if not filtered_students:
        st.info("No students match the current filter.")
    else:
        # Group by cohort and render in stable order
        cohort_ids_seen = []
        for student in filtered_students:
            cid = student["cohort_id"]
            if cid not in cohort_ids_seen:
                cohort_ids_seen.append(cid)

        for cid in cohort_ids_seen:
            cohort = cohort_map[cid]
            st.subheader(
                f"{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}"
                f" — {cohort['teacher']}"
            )
            cohort_students = [s for s in filtered_students if s["cohort_id"] == cid]
            st.caption(f"{len(cohort_students)} students in this group")
            for student in cohort_students:
                _render_student(student, cohort)
