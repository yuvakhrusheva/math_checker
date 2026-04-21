"""SQLite database layer for math_checker."""
import sqlite3
from pathlib import Path

DB_PATH = "data/math_checker.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cohorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    gdrive_folder_url TEXT NOT NULL,
    gdrive_folder_id TEXT NOT NULL,
    school TEXT NOT NULL,
    teacher TEXT NOT NULL,
    class_number INTEGER NOT NULL,
    class_letter TEXT NOT NULL,
    in_project INTEGER NOT NULL DEFAULT 1,
    language TEXT NOT NULL CHECK(language IN ('ru', 'az')),
    test_date TEXT NOT NULL,
    grade INTEGER NOT NULL CHECK(grade IN (2, 3)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'done', 'done_with_errors'))
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id INTEGER NOT NULL REFERENCES cohorts(id),
    gdrive_file_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    recognized_name TEXT,
    detected_variant INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'processed',
                         'requires_review', 'unreadable', 'error')),
    review_status TEXT CHECK(review_status IN ('pending', 'done')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    task_number INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    recognized_answer TEXT,
    score REAL NOT NULL DEFAULT 0,
    max_score REAL NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('low', 'high')),
    grading_notes TEXT,
    manually_corrected INTEGER NOT NULL DEFAULT 0,
    UNIQUE(student_id, task_number)
);
"""


def get_connection() -> sqlite3.Connection:
    """Open DB connection with WAL mode and dict-like row access."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create all tables (idempotent). Safe to call multiple times."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------

def create_cohort(
    gdrive_folder_url: str,
    gdrive_folder_id: str,
    school: str,
    teacher: str,
    class_number: int,
    class_letter: str,
    language: str,
    test_date: str,
    grade: int,
    in_project: int = 1,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO cohorts
                (gdrive_folder_url, gdrive_folder_id, school, teacher,
                 class_number, class_letter, language, test_date, grade, in_project)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (gdrive_folder_url, gdrive_folder_id, school, teacher,
             class_number, class_letter, language, test_date, grade, in_project),
        )
        return cur.lastrowid


def get_cohort(cohort_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM cohorts WHERE id = ?", (cohort_id,)).fetchone()


def list_cohorts() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM cohorts ORDER BY created_at DESC").fetchall()


def update_cohort_status(cohort_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE cohorts SET status = ? WHERE id = ?", (status, cohort_id))


def update_cohort_metadata(cohort_id: int, **fields) -> None:
    """Update editable metadata fields. Raises ValueError if cohort is not pending."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM cohorts WHERE id = ?", (cohort_id,)).fetchone()
        if row is None:
            raise ValueError(f"Cohort {cohort_id} not found")
        if row["status"] != "pending":
            raise ValueError(
                f"Cannot update cohort {cohort_id}: status is '{row['status']}', must be 'pending'"
            )
        if not fields:
            return
        allowed = {"school", "teacher", "class_number", "class_letter", "language",
                   "test_date", "grade", "gdrive_folder_url", "gdrive_folder_id", "in_project"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unknown cohort fields: {invalid}")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE cohorts SET {set_clause} WHERE id = ?",  # noqa: S608 — field names validated above
            (*fields.values(), cohort_id),
        )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

def create_student(cohort_id: int, gdrive_file_id: str, filename: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO students (cohort_id, gdrive_file_id, filename) VALUES (?, ?, ?)",
            (cohort_id, gdrive_file_id, filename),
        )
        return cur.lastrowid


def get_student(student_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()


def list_students_by_cohort(cohort_id: int) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM students WHERE cohort_id = ? ORDER BY filename", (cohort_id,)
        ).fetchall()


def update_student_status(student_id: int, status: str, error_message: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE students SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, student_id),
        )


def update_student_variant(student_id: int, detected_variant: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE students SET detected_variant = ? WHERE id = ?",
            (detected_variant, student_id),
        )


def update_student_recognized_name(student_id: int, name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE students SET recognized_name = ? WHERE id = ?",
            (name, student_id),
        )


def list_requires_review() -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM students WHERE status = 'requires_review' ORDER BY cohort_id, filename"
        ).fetchall()


# ---------------------------------------------------------------------------
# Task results
# ---------------------------------------------------------------------------

def save_task_result(
    student_id: int,
    task_number: int,
    page_number: int,
    recognized_answer: str | None,
    score: float,
    max_score: float,
    confidence: str,
    grading_notes: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_results
                (student_id, task_number, page_number, recognized_answer,
                 score, max_score, confidence, grading_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (student_id, task_number, page_number, recognized_answer,
             score, max_score, confidence, grading_notes),
        )


def get_task_results(student_id: int) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM task_results WHERE student_id = ? ORDER BY task_number",
            (student_id,),
        ).fetchall()


def update_task_result(result_id: int, recognized_answer: str, score: float, manually_corrected: bool = True) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE task_results
            SET recognized_answer = ?, score = ?, manually_corrected = ?
            WHERE id = ?
            """,
            (recognized_answer, score, int(manually_corrected), result_id),
        )
