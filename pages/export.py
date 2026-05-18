"""Export screen — Task 11.

Provides an Export button that generates a timestamped Excel file with
grading results, and a download link. Also shows unreadable students with
their error messages. Interface text in English (CA-28).
Supports CA-20 through CA-25.
"""
import streamlit as st

import src.db as db
import src.exporter as exporter


st.title("Export")

st.write("Export all grading results to an Excel file (.xlsx) with two sheets: Answers and Scores.")

if st.button("📥 Export to Excel"):
    try:
        path = exporter.export()
        with open(path, "rb") as f:
            file_bytes = f.read()
        st.download_button(
            label=f"Download {path.name}",
            data=file_bytes,
            file_name=path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.success(f"Export ready: {path.name}")
    except Exception as exc:
        st.error(f"Export failed: {exc}")

st.divider()

# Error list — unreadable students
st.subheader("Unreadable Students")
st.caption(
    "Students whose PDFs could not be processed are excluded from the export. "
    "Review them below."
)

cohorts = db.list_cohorts()
cohort_map = {c["id"]: c for c in cohorts}

any_unreadable = False
for cohort in cohorts:
    students = db.list_students_by_cohort(cohort["id"])
    unreadable = [s for s in students if s["status"] == "unreadable"]
    if not unreadable:
        continue
    any_unreadable = True
    st.write(
        f"**{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}**"
    )
    for student in unreadable:
        name = student["recognized_name"] or student["filename"]
        error = student["error_message"] or "Unknown error"
        st.write(f"- `{name}`: {error}")

if not any_unreadable:
    st.info("No unreadable students.")
