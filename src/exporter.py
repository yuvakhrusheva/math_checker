"""Excel exporter for math_checker results (Task 11).

Generates a timestamped .xlsx file with two sheets:
  - "Answers" — raw task answers per student
  - "Scores"  — computed scores, totals, and performance levels

Students with status=unreadable are excluded.
Students with status=error get "ERROR" in all task columns.
Students with review_status=pending get "PENDING" in all task columns.

Performance levels (grade 3 only):
  81-100 → Advanced
  61-80  → Basic
  31-60  → Minimal
  0-30   → Insufficient
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

import src.db as db


# ---------------------------------------------------------------------------
# Testable helper
# ---------------------------------------------------------------------------

def _performance_level(total_score: float) -> str:
    """Return grade-3 performance level string for the given total score."""
    if total_score >= 81:
        return "Advanced"
    elif total_score >= 61:
        return "Basic"
    elif total_score >= 31:
        return "Minimal"
    else:
        return "Insufficient"


# ---------------------------------------------------------------------------
# Export function
# ---------------------------------------------------------------------------

_TASK_NUMS = list(range(1, 17))


def export(output_dir: str = "exports") -> Path:
    """
    Query DB, build DataFrames, and write to a timestamped .xlsx file.
    Returns the Path of the created file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = Path(output_dir) / f"results_{timestamp}.xlsx"

    # Fetch all students (excluding unreadable) joined with cohort data
    with db.get_connection() as conn:
        students = conn.execute(
            """
            SELECT
                s.id          AS student_id,
                s.cohort_id,
                s.recognized_name,
                s.detected_variant,
                s.status,
                s.review_status,
                s.error_message,
                c.school,
                c.teacher,
                c.class_number,
                c.class_letter,
                c.test_date,
                c.grade
            FROM students s
            JOIN cohorts c ON s.cohort_id = c.id
            WHERE s.status != 'unreadable'
            ORDER BY c.school, c.class_number, c.class_letter, s.id
            """
        ).fetchall()

    answer_rows = []
    score_rows = []

    for student in students:
        sid = student["student_id"]
        is_error = student["status"] == "error"
        # Treat requires_review students as pending regardless of whether
        # review_status was explicitly set — ERROR takes precedence
        is_pending = (
            student["review_status"] == "pending"
            or student["status"] == "requires_review"
        )
        if is_error:
            marker = "ERROR"
        elif is_pending:
            marker = "PENDING"
        else:
            marker = None

        # Fetch task results keyed by task_number
        results = {r["task_number"]: r for r in db.get_task_results(sid)}

        class_str = f"{student['class_number']}{student['class_letter']}"
        base = {
            "school": student["school"],
            "class": class_str,
            "teacher": student["teacher"],
            "test_date": student["test_date"],
            "student_id": sid,
        }

        answer_row = dict(base)
        answer_row["recognized_name"] = student["recognized_name"] or ""
        score_row = dict(base)

        total_score = 0.0
        for t in _TASK_NUMS:
            if marker:
                answer_row[f"task_{t}_answer"] = marker
                score_row[f"task_{t}_score"] = marker
            else:
                r = results.get(t)
                answer_row[f"task_{t}_answer"] = r["recognized_answer"] if r else ""
                task_score = float(r["score"]) if r else 0.0
                score_row[f"task_{t}_score"] = task_score
                total_score += task_score

        if marker:
            score_row["total_score"] = None
            score_row["performance_level"] = ""
        else:
            score_row["total_score"] = total_score
            grade = student["grade"]
            if grade == 3:
                score_row["performance_level"] = _performance_level(total_score)
            else:
                score_row["performance_level"] = ""

        answer_rows.append(answer_row)
        score_rows.append(score_row)

    df_answers = pd.DataFrame(answer_rows) if answer_rows else _empty_answers_df()
    df_scores = pd.DataFrame(score_rows) if score_rows else _empty_scores_df()

    with pd.ExcelWriter(str(filename), engine="openpyxl") as writer:
        df_answers.to_excel(writer, sheet_name="Answers", index=False)
        df_scores.to_excel(writer, sheet_name="Scores", index=False)

    return filename


def _empty_answers_df() -> pd.DataFrame:
    cols = (
        ["school", "class", "teacher", "test_date", "student_id", "recognized_name"]
        + [f"task_{t}_answer" for t in _TASK_NUMS]
    )
    return pd.DataFrame(columns=cols)


def _empty_scores_df() -> pd.DataFrame:
    cols = (
        ["school", "class", "teacher", "test_date", "student_id"]
        + [f"task_{t}_score" for t in _TASK_NUMS]
        + ["total_score", "performance_level"]
    )
    return pd.DataFrame(columns=cols)
