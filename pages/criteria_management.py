"""Criteria Management page — upload and view grading criteria JSON files."""
import streamlit as st
import pandas as pd

from src.criteria_loader import save_criteria_file, list_available_combinations
import src.auth as auth

# Защита: страница доступна только залогиненным пользователям.
auth.require_login()


st.title("📋 Criteria Management")
st.caption("Upload grading criteria JSON files and view which grade/language/variant combinations are loaded.")

# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------
st.subheader("Upload Criteria File")
st.markdown(
    "Filename must match the pattern `grade[2|3]_[ru|az]_v[1|2].json`  \n"
    "Internal `grade`, `language`, and `variant` fields must match the filename."
)

uploaded = st.file_uploader(
    "Select a criteria JSON file",
    type=["json"],
    help="Only .json files matching the naming convention are accepted.",
)

if uploaded is not None:
    filename = uploaded.name
    content = uploaded.read()
    try:
        save_criteria_file(filename, content)
        stem = filename.removesuffix(".json")
        st.success(f"Loaded: {stem}")
    except ValueError as exc:
        st.error(f"Invalid criteria: {exc}")

# ---------------------------------------------------------------------------
# Loaded criteria grid
# ---------------------------------------------------------------------------
st.subheader("Loaded Criteria")

combos = list_available_combinations()

GRADES = [2, 3]
LANGUAGES = ["ru", "az"]

# Build a lookup: (grade, language) → set of variants
loaded: dict[tuple, set] = {}
for c in combos:
    key = (c["grade"], c["language"])
    loaded.setdefault(key, set()).add(c["variant"])

# Build display table
rows = []
for grade in GRADES:
    row = {"Grade": f"Grade {grade}"}
    for lang in LANGUAGES:
        variants = loaded.get((grade, lang), set())
        if variants:
            parts = [f"v{v} ✓" for v in sorted(variants)]
            row[lang.upper()] = ", ".join(parts)
        else:
            row[lang.upper()] = "—"
    rows.append(row)

df = pd.DataFrame(rows).set_index("Grade")
st.dataframe(df, use_container_width=True)

if not combos:
    st.info("No criteria files loaded yet. Upload files above to get started.")
