"""Main screen (Cohort Queue) — v5 (stage14).

stage14:
- Используется list_cohorts_for_user — куратор видит только свои когорты
  (admin видит всё).
- При импорте прокидывается owner_user_id текущего пользователя — все
  созданные когорты получают его как владельца.
- Manual single-folder add тоже привязывает когорту к текущему юзеру.
"""
from datetime import date as _date

import streamlit as st

import src.db as db
import src.queue_processor as queue_processor
import src.criteria_loader as criteria_loader
import src.drive as drive
import src.drive_walker as drive_walker
import src.auth as auth

# Защита: страница доступна только залогиненным пользователям.
auth.require_login()
_CU = auth.current_user() or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_gdrive_url(url):
    if not url or "drive.google.com" not in url:
        return "URL must be a Google Drive folder URL containing 'drive.google.com'"
    return None


def should_disable_start_button():
    return queue_processor.is_running()


def try_browse_root(gdrive_url: str):
    err = validate_gdrive_url(gdrive_url)
    if err:
        return None, err
    try:
        root_id = drive.extract_folder_id(gdrive_url)
    except ValueError as exc:
        return None, f"Invalid Google Drive URL: {exc}"
    try:
        service = drive.get_service()
    except Exception as exc:
        return None, f"Could not connect to Drive: {exc}"
    try:
        entries = drive_walker.discover_options_shallow(service, root_id)
    except Exception as exc:
        return None, f"Browse failed: {exc}"
    return {"root_id": root_id, "url": gdrive_url, "entries": entries}, None


def try_import_selected(gdrive_url: str, filters: dict, progress_callback=None):
    err = validate_gdrive_url(gdrive_url)
    if err:
        return None, err
    try:
        root_id = drive.extract_folder_id(gdrive_url)
    except ValueError as exc:
        return None, f"Invalid Google Drive URL: {exc}"
    try:
        service = drive.get_service()
    except Exception as exc:
        return None, f"Could not connect to Drive: {exc}"
    try:
        summary = drive_walker.scan_and_import_root(
            service, root_id, root_folder_url=gdrive_url,
            progress_callback=progress_callback,
            filters=filters,
            owner_user_id=_CU.get("id"),
        )
    except Exception as exc:
        return None, f"Import failed: {exc}"
    return summary, None


def try_add_cohort(
    gdrive_url, school, teacher, class_number, class_letter,
    language, test_date, grade,
):
    err = validate_gdrive_url(gdrive_url)
    if err:
        return None, err
    if not criteria_loader.criteria_exists(grade, language):
        return None, (
            f"No criteria loaded for Grade {grade} / {language.upper()}. "
            "Upload the criteria JSON file on the Criteria Management page first."
        )
    try:
        folder_id = drive.extract_folder_id(gdrive_url)
    except ValueError as exc:
        return None, f"Invalid Google Drive URL: {exc}"
    try:
        service = drive.get_service()
        pdfs = drive.list_pdfs(service, folder_id)
    except Exception as exc:
        return None, f"Could not read Drive folder: {exc}"
    if not pdfs:
        return None, "No PDF files found in the Drive folder."
    cohort_id = db.create_cohort(
        gdrive_folder_url=gdrive_url, gdrive_folder_id=folder_id,
        school=school, teacher=teacher, class_number=class_number,
        class_letter=class_letter, language=language,
        test_date=str(test_date), grade=grade,
        owner_user_id=_CU.get("id"),
    )
    for pdf in pdfs:
        existing = db.find_student_by_file(cohort_id, pdf["id"])
        if existing is None:
            db.create_student(cohort_id, pdf["id"], pdf["name"])
    return cohort_id, None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    "pending": "⏳ Pending",
    "processing": "⚙️ Processing",
    "done": "✅ Done",
    "done_with_errors": "⚠️ Done (errors)",
}

_LANG_LABELS = {"ru": "Russian", "az": "Azerbaijani"}
_SECTOR_LABEL = {"ru": "Ру сектор", "az": "Аз сектор"}


def _render_cohort_table(cohorts):
    if not cohorts:
        st.info("У тебя пока нет когорт. Импортируй их через форму ниже.")
        return
    for cohort in cohorts:
        students = db.list_students_by_cohort(cohort["id"])
        total = len(students)
        processed = sum(
            1 for s in students
            if s["status"] in ("processed", "requires_review", "unreadable", "error")
        )
        status_label = _STATUS_LABELS.get(cohort["status"], cohort["status"])
        n_unfinished = db.count_unfinished_in_cohort(cohort["id"])
        failed = db.list_failed_students_in_cohort(cohort["id"])
        status_display = (
            f"{status_label} ({n_unfinished}⏳)" if n_unfinished else status_label
        )

        cols = st.columns([2, 1, 1, 1, 1, 2, 1, 1])
        cols[0].write(f"**{cohort['school']}**")
        cols[1].write(f"{cohort['class_number']}{cohort['class_letter']}")
        cols[2].write(cohort["teacher"])
        cols[3].write(cohort["test_date"])
        cols[4].write(_LANG_LABELS.get(cohort["language"], cohort["language"]))
        cols[5].write(status_display)
        cols[6].write(f"{processed}/{total}")

        with cols[7]:
            if cohort["status"] == "pending":
                if st.button("✏️", key=f"edit_{cohort['id']}", help="Edit"):
                    st.session_state[f"editing_{cohort['id']}"] = True
                if st.button("🗑️", key=f"del_{cohort['id']}", help="Delete"):
                    db.update_cohort_metadata(cohort["id"], in_project=0)
                    st.rerun()
            if n_unfinished > 0 and not queue_processor.is_running():
                if st.button("🔄", key=f"retry_{cohort['id']}",
                             help=f"Сбросить {n_unfinished} незавершённых работ"):
                    n = db.reset_unfinished_students_in_cohort(cohort["id"])
                    st.success(f"{n} работ сброшено в очередь. Нажми ▶️ Start Processing.")
                    st.rerun()

        if failed:
            with st.expander(f"⚠️ {len(failed)} работ с проблемами"):
                for s in failed:
                    msg = s["error_message"] or f"застряла в статусе «{s['status']}»"
                    st.write(f"- **{s['filename']}**: {msg}")
        if st.session_state.get(f"editing_{cohort['id']}"):
            _render_edit_form(cohort)


def _render_edit_form(cohort):
    with st.form(key=f"edit_form_{cohort['id']}"):
        st.write(f"**Edit cohort {cohort['id']}**")
        school = st.text_input("School", value=cohort["school"])
        teacher = st.text_input("Teacher", value=cohort["teacher"])
        class_number = st.number_input("Class number", min_value=1, max_value=11,
                                       value=cohort["class_number"])
        class_letter = st.text_input("Class letter", value=cohort["class_letter"])
        test_date = st.text_input("Test date (YYYY-MM-DD)", value=cohort["test_date"])
        language = st.selectbox("Language", ["ru", "az"],
                                index=0 if cohort["language"] == "ru" else 1)
        col1, col2 = st.columns(2)
        if col1.form_submit_button("Save"):
            try:
                db.update_cohort_metadata(
                    cohort["id"], school=school, teacher=teacher,
                    class_number=int(class_number), class_letter=class_letter,
                    test_date=test_date, language=language,
                )
                del st.session_state[f"editing_{cohort['id']}"]
                st.rerun()
            except ValueError as exc:
                st.error(f"Cannot edit: {exc}")
        if col2.form_submit_button("Cancel"):
            del st.session_state[f"editing_{cohort['id']}"]
            st.rerun()


# Root import — two-phase: Browse, then Import selected
_BROWSE_KEY = "root_import_browse_data"


def _render_root_import_form():
    with st.expander("📁 Import from root folder (auto-detect cohorts)", expanded=True):
        st.caption(
            "**Шаг 1.** Вставь ссылку на корневую папку и нажми «Browse». "
            "**Шаг 2.** Выбери, что именно ты хочешь проверять. Импортированные "
            "когорты будут видны только тебе (если ты не admin)."
        )

        root_url = st.text_input(
            "Root folder URL",
            value=st.session_state.get(_BROWSE_KEY, {}).get("url", ""),
            placeholder="https://drive.google.com/drive/folders/<id>",
            key="root_import_url",
        )

        browse_col, reset_col = st.columns([1, 1])
        with browse_col:
            if st.button("🔍 Browse folder structure"):
                with st.spinner("Walking Drive folder structure…"):
                    data, error = try_browse_root(root_url)
                if error:
                    st.error(error)
                else:
                    if not data["entries"]:
                        st.warning("No teacher folders found. Check structure / access.")
                    st.session_state[_BROWSE_KEY] = data
                    st.rerun()
        with reset_col:
            if _BROWSE_KEY in st.session_state and st.button("↻ Reset"):
                del st.session_state[_BROWSE_KEY]
                st.rerun()

        browse_data = st.session_state.get(_BROWSE_KEY)
        if not browse_data:
            return

        entries = browse_data["entries"]
        if not entries:
            return

        st.success(f"Found {len(entries)} teacher folders. Pick what to import:")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            sectors = sorted({e.language for e in entries})
            sector_choice = st.selectbox(
                "Sector", ["All"] + [_SECTOR_LABEL.get(s, s) for s in sectors],
                key="import_sector",
            )
        sector_value = None
        if sector_choice != "All":
            for code, lbl in _SECTOR_LABEL.items():
                if lbl == sector_choice:
                    sector_value = code
                    break

        after_s = [e for e in entries if sector_value is None or e.language == sector_value]
        with c2:
            grades = sorted({e.grade for e in after_s})
            grade_choice = st.selectbox(
                "Grade", ["All"] + [f"{g} класс" for g in grades], key="import_grade",
            )
        grade_value = int(grade_choice.split()[0]) if grade_choice != "All" else None
        after_g = [e for e in after_s if grade_value is None or e.grade == grade_value]

        with c3:
            schools = sorted({e.school for e in after_g})
            school_choice = st.selectbox(
                "School", ["All"] + schools, key="import_school",
            )
        school_value = None if school_choice == "All" else school_choice
        after_sc = [e for e in after_g if school_value is None or e.school == school_value]

        with c4:
            teachers = sorted({e.teacher for e in after_sc})
            teacher_choice = st.selectbox(
                "Teacher", ["All"] + teachers, key="import_teacher",
            )
        teacher_value = None if teacher_choice == "All" else teacher_choice
        after_t = [e for e in after_sc if teacher_value is None or e.teacher == teacher_value]

        st.caption(f"**Selection:** {len(after_t)} teacher folders match.")

        if st.button("📥 Import selected"):
            if not after_t:
                st.warning("Nothing to import.")
                return
            filters = {
                "sector": sector_value, "grade": grade_value,
                "school": school_value, "teacher": teacher_value,
            }
            status_box = st.status("Starting import…", expanded=True)

            def on_progress(stage: str, current: int, total: int) -> None:
                if stage == "walking":
                    status_box.update(label=f"🔍 Walking… ({current})")
                elif stage == "saving":
                    if current < total:
                        status_box.update(label=f"💾 Saving — {current}/{total}…")
                    else:
                        status_box.update(label=f"💾 Saved {total} ✓")

            summary, error = try_import_selected(
                browse_data["url"], filters=filters, progress_callback=on_progress,
            )
            if error:
                status_box.update(label="❌ Import failed", state="error")
                st.error(f"Import failed: {error}")
                return
            status_box.update(label="✅ Import complete", state="complete")
            st.success(
                f"Created {summary.cohorts_created} cohorts, "
                f"{summary.students_created} students. "
                f"Skipped {summary.students_skipped_dupes} duplicates. "
                f"{summary.students_flagged_for_review} flagged for review."
            )
            if summary.errors:
                with st.expander(f"⚠️ {len(summary.errors)} issues"):
                    for line in summary.errors:
                        st.write(f"- {line}")
            st.rerun()


def _render_add_form():
    with st.expander("➕ Add Cohort (manual, single folder)"):
        with st.form("add_cohort_form"):
            gdrive_url = st.text_input("Google Drive folder URL")
            school = st.text_input("School")
            col1, col2 = st.columns(2)
            class_number = col1.number_input("Class number", min_value=1, max_value=11, value=3)
            class_letter = col2.text_input("Class letter", value="A")
            teacher = st.text_input("Teacher name")
            test_date = st.date_input("Test date", value=_date.today())
            col3, col4 = st.columns(2)
            language = col3.selectbox("Language", ["ru", "az"],
                                      format_func=lambda x: _LANG_LABELS[x])
            grade = col4.selectbox("Grade", [2, 3])
            submitted = st.form_submit_button("Add Cohort")
        if submitted:
            cohort_id, error = try_add_cohort(
                gdrive_url=gdrive_url, school=school, teacher=teacher,
                class_number=int(class_number), class_letter=class_letter,
                language=language, test_date=str(test_date), grade=int(grade),
            )
            if error:
                st.error(f"Cannot add cohort: {error}")
            else:
                pdf_count = len(db.list_students_by_cohort(cohort_id))
                st.success(f"Cohort added (ID {cohort_id}) — {pdf_count} PDFs queued")
                st.rerun()


@st.fragment(run_every=2)
def _render_progress():
    # Прогресс смотрим по своим когортам (для admin — по всем).
    cohorts = db.list_cohorts_for_user(_CU.get("id"), _CU.get("role"))
    processing = [c for c in cohorts if c["status"] == "processing"]
    if not processing:
        st.caption("No cohorts currently processing.")
        return
    st.subheader("Live Progress")
    for cohort in processing:
        students = db.list_students_by_cohort(cohort["id"])
        total = len(students)
        if total == 0:
            continue
        done = sum(1 for s in students if s["status"] not in ("pending", "processing"))
        errors = sum(1 for s in students if s["status"] == "error")
        reviews = sum(1 for s in students if s["status"] == "requires_review")
        label = f"{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}"
        st.write(f"**{label}** — {done}/{total} processed | {reviews} for review | {errors} errors")
        st.progress(done / total if total else 0)


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

st.title("Cohort Queue")

if _CU.get("role") == "admin":
    st.caption("👑 Ты admin — видишь все когорты всех кураторов.")
else:
    st.caption("Видны только твои когорты. Когорты других кураторов скрыты.")

hdr = st.columns([2, 1, 1, 1, 1, 2, 1, 1])
hdr[0].write("**School**")
hdr[1].write("**Class**")
hdr[2].write("**Teacher**")
hdr[3].write("**Test Date**")
hdr[4].write("**Language**")
hdr[5].write("**Status**")
hdr[6].write("**PDFs**")
hdr[7].write("**Actions**")

st.divider()

cohorts = [
    c for c in db.list_cohorts_for_user(_CU.get("id"), _CU.get("role"))
    if c["in_project"]
]
_render_cohort_table(cohorts)

st.divider()
_render_root_import_form()
_render_add_form()

st.divider()

start_col, stop_col, status_col = st.columns([1, 1, 3])
with start_col:
    if st.button("▶️ Start Processing", disabled=should_disable_start_button()):
        queue_processor.start(started_by=_CU.get("username"))
        st.success("Processing started.")
        st.rerun()
with stop_col:
    if st.button("⏹ Stop Processing",
                 disabled=not queue_processor.is_running(),
                 help="Останавливает обработку после текущего ученика"):
        queue_processor.force_reset()
        st.warning("Stop requested. Через 10-30 сек снова сможешь нажать ▶️.")
        st.rerun()
with status_col:
    if queue_processor.is_running():
        st.info("⚙️ Queue processor is running…")
    else:
        st.caption("Queue processor is idle.")

st.divider()
_render_progress()
