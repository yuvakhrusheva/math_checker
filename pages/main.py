"""Main screen (Cohort Queue) — Task 9.

Provides operator workflow: view all cohorts, add new cohorts with validation,
edit/delete pending cohorts, start the processing queue, and watch live progress.
All UI text in English (CA-28). Supports CA-4 through CA-9.
"""
import streamlit as st

import src.db as db
import src.queue_processor as queue_processor
import src.criteria_loader as criteria_loader
import src.drive as drive


# ---------------------------------------------------------------------------
# Testable helper functions (imported by tests/unit/test_main_helpers.py)
# ---------------------------------------------------------------------------

def validate_gdrive_url(url: str | None) -> str | None:
    """Return None if valid Google Drive URL, or error message string if invalid."""
    if not url or "drive.google.com" not in url:
        return "URL must be a Google Drive folder URL containing 'drive.google.com'"
    return None


def should_disable_start_button() -> bool:
    """Return True if the Start Processing button should be disabled."""
    return queue_processor.is_running()


def try_add_cohort(
    gdrive_url: str,
    school: str,
    teacher: str,
    class_number: int,
    class_letter: str,
    language: str,
    test_date: str,
    grade: int,
) -> tuple[int | None, str | None]:
    """
    Validate inputs and create a new cohort.
    Returns (cohort_id, None) on success or (None, error_message) on failure.
    """
    url_error = validate_gdrive_url(gdrive_url)
    if url_error:
        return None, url_error

    if not criteria_loader.criteria_exists(grade, language):
        return None, (
            f"No criteria loaded for Grade {grade} / {language.upper()}. "
            "Please upload the criteria JSON file on the Criteria Management page first."
        )

    try:
        folder_id = drive.extract_folder_id(gdrive_url)
    except ValueError as exc:
        return None, f"Invalid Google Drive URL: {exc}"

    cohort_id = db.create_cohort(
        gdrive_folder_url=gdrive_url,
        gdrive_folder_id=folder_id,
        school=school,
        teacher=teacher,
        class_number=class_number,
        class_letter=class_letter,
        language=language,
        test_date=str(test_date),
        grade=grade,
    )
    return cohort_id, None


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

_STATUS_LABELS = {
    "pending": "⏳ Pending",
    "processing": "⚙️ Processing",
    "done": "✅ Done",
    "done_with_errors": "⚠️ Done (errors)",
}

_LANG_LABELS = {"ru": "Russian", "az": "Azerbaijani"}


def _render_cohort_table(cohorts):
    """Render the cohort queue table with Edit/Delete actions for pending cohorts."""
    if not cohorts:
        st.info("No cohorts in queue. Add one below.")
        return

    for cohort in cohorts:
        students = db.list_students_by_cohort(cohort["id"])
        total = len(students)
        processed = sum(
            1 for s in students
            if s["status"] in ("processed", "requires_review", "unreadable", "error")
        )
        status_label = _STATUS_LABELS.get(cohort["status"], cohort["status"])

        cols = st.columns([2, 1, 1, 1, 1, 2, 1, 1])
        cols[0].write(f"**{cohort['school']}**")
        cols[1].write(f"{cohort['class_number']}{cohort['class_letter']}")
        cols[2].write(cohort["teacher"])
        cols[3].write(cohort["test_date"])
        cols[4].write(_LANG_LABELS.get(cohort["language"], cohort["language"]))
        cols[5].write(status_label)
        cols[6].write(f"{processed}/{total}")

        if cohort["status"] == "pending":
            with cols[7]:
                if st.button("✏️", key=f"edit_{cohort['id']}", help="Edit"):
                    st.session_state[f"editing_{cohort['id']}"] = True
                if st.button("🗑️", key=f"del_{cohort['id']}", help="Delete"):
                    # Remove cohort (only pending — guard enforced by UI)
                    db.update_cohort_status(cohort["id"], "done")  # soft-delete via status
                    st.rerun()

        # Inline edit form
        if st.session_state.get(f"editing_{cohort['id']}"):
            _render_edit_form(cohort)


def _render_edit_form(cohort):
    """Render an inline edit form for a pending cohort."""
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
                    cohort["id"],
                    school=school,
                    teacher=teacher,
                    class_number=int(class_number),
                    class_letter=class_letter,
                    test_date=test_date,
                    language=language,
                )
                del st.session_state[f"editing_{cohort['id']}"]
                st.rerun()
            except ValueError as exc:
                st.error(f"Cannot edit: {exc}")
        if col2.form_submit_button("Cancel"):
            del st.session_state[f"editing_{cohort['id']}"]
            st.rerun()


def _render_add_form():
    """Render the Add Cohort expandable form."""
    with st.expander("➕ Add Cohort"):
        with st.form("add_cohort_form"):
            gdrive_url = st.text_input("Google Drive folder URL")
            school = st.text_input("School")
            col1, col2 = st.columns(2)
            class_number = col1.number_input("Class number", min_value=1, max_value=11, value=3)
            class_letter = col2.text_input("Class letter", value="A")
            teacher = st.text_input("Teacher name")
            test_date = st.date_input("Test date")
            col3, col4 = st.columns(2)
            language = col3.selectbox("Language", ["ru", "az"],
                                      format_func=lambda x: _LANG_LABELS[x])
            grade = col4.selectbox("Grade", [2, 3])

            submitted = st.form_submit_button("Add Cohort")

        if submitted:
            cohort_id, error = try_add_cohort(
                gdrive_url=gdrive_url,
                school=school,
                teacher=teacher,
                class_number=int(class_number),
                class_letter=class_letter,
                language=language,
                test_date=str(test_date),
                grade=int(grade),
            )
            if error:
                st.error(f"Cannot add cohort: {error}")
            else:
                st.success(f"Cohort added (ID {cohort_id})")
                st.rerun()


@st.fragment(run_every=2)
def _render_progress():
    """Live progress section — updates every 2 seconds without full page reload."""
    cohorts = db.list_cohorts()
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
        done = sum(
            1 for s in students
            if s["status"] not in ("pending", "processing")
        )
        errors = sum(1 for s in students if s["status"] == "error")
        reviews = sum(1 for s in students if s["status"] == "requires_review")

        label = f"{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}"
        st.write(f"**{label}** — {done}/{total} processed | {reviews} for review | {errors} errors")
        st.progress(done / total if total else 0)


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------

st.title("Cohort Queue")

# Column headers
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

cohorts = db.list_cohorts()
_render_cohort_table(cohorts)

st.divider()
_render_add_form()

st.divider()

# Start Processing button
btn_col, status_col = st.columns([1, 3])
with btn_col:
    if st.button("▶️ Start Processing", disabled=should_disable_start_button()):
        queue_processor.start()
        st.success("Processing started.")
        st.rerun()

with status_col:
    if queue_processor.is_running():
        st.info("⚙️ Queue processor is running…")
    else:
        st.caption("Queue processor is idle.")

st.divider()
_render_progress()
