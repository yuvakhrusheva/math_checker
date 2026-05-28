"""SQLAlchemy-based database layer for math_checker.

Поддерживает два бэкенда:
- SQLite (по умолчанию, для локальной разработки)
- PostgreSQL (для продакшена)

Выбирается через переменную окружения DATABASE_URL. Если не задана —
используется sqlite:///<DB_PATH> (старое поведение).

Интерфейс функций (имена, аргументы, возвращаемые значения) сохранён.
SELECT-функции возвращают sqlalchemy.RowMapping — словарь-подобный объект,
который поддерживает row["col"], row.col, row[0]. Это совместимо со старым
sqlite3.Row, поэтому вызывающий код (queue_processor, pages, тесты) не правится.

Также экспортируется backward-compat функция get_connection() — возвращает
DBAPI-соединение с row_factory=sqlite3.Row. Нужна для кода, который выполняет
raw SQL (например, exporter.py с большим JOIN-запросом).
"""
import os
import sqlite3
from pathlib import Path

from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Float, ForeignKey,
    CheckConstraint, UniqueConstraint,
    create_engine, insert, select, update, text,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH: str = "data/math_checker.db"


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    abs_path = os.path.abspath(DB_PATH)
    return f"sqlite:///{abs_path}"


_engine_cache: dict = {}


def get_engine():
    url = _resolve_database_url()
    if url not in _engine_cache:
        # Для SQLite — заранее создаём папку, в которой будет лежать .db файл.
        # SQLite не умеет создавать БД в несуществующей директории.
        if url.startswith("sqlite:///"):
            db_file = url.replace("sqlite:///", "", 1)
            if db_file and db_file != ":memory:":
                Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            with engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()
        _engine_cache[url] = engine
    return _engine_cache[url]


def reset_engine_cache() -> None:
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()


# ---------------------------------------------------------------------------
# Backward-compat: get_connection
# ---------------------------------------------------------------------------

class _ConnectionShim:
    """Тонкая обёртка над sqlite3.Connection / psycopg2.connection для совместимости.

    Старый код делает:
        with db.get_connection() as conn:
            rows = conn.execute("SELECT ...").fetchall()
            row["col_name"]

    Этот shim обеспечивает работу того же паттерна и для SQLite, и для Postgres.
    Для SQLite — устанавливает row_factory=sqlite3.Row, чтобы row["col_name"]
    работал. Для Postgres — оборачивает курсор в DictCursor-like.
    """

    def __init__(self, engine):
        self._engine = engine
        self._raw = None

    def __enter__(self):
        self._raw = self._engine.raw_connection()
        if self._engine.dialect.name == "sqlite":
            # raw — это объект из sqlalchemy.pool, обернувший sqlite3.Connection.
            # Реальное соединение — .driver_connection (или .connection).
            real = getattr(self._raw, "driver_connection", None) or self._raw.connection
            real.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()
        return False

    def execute(self, sql, params=()):
        cursor = self._raw.cursor()
        cursor.execute(sql, params)
        return cursor

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()


def get_connection():
    """Backward-compat: вернуть DBAPI-соединение, где row["col_name"] работает.

    Использовать для raw-SQL кода (см. exporter.py). Для типичных CRUD-операций
    предпочитайте функции этого модуля (они уже возвращают RowMapping).
    """
    return _ConnectionShim(get_engine())


# ---------------------------------------------------------------------------
# Schema (SQLAlchemy Core MetaData)
# ---------------------------------------------------------------------------

metadata = MetaData()

cohorts = Table(
    "cohorts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String, server_default=text("CURRENT_TIMESTAMP")),
    Column("gdrive_folder_url", String, nullable=False),
    Column("gdrive_folder_id", String, nullable=False),
    Column("school", String, nullable=False),
    Column("teacher", String, nullable=False),
    Column("class_number", Integer, nullable=False),
    Column("class_letter", String, nullable=False),
    Column("in_project", Integer, nullable=False, server_default="1"),
    Column("language", String, nullable=False),
    Column("test_date", String, nullable=False),
    Column("grade", Integer, nullable=False),
    Column("status", String, nullable=False, server_default="pending"),
    CheckConstraint("language IN ('ru', 'az')", name="ck_cohorts_language"),
    CheckConstraint("grade IN (2, 3)", name="ck_cohorts_grade"),
    CheckConstraint(
        "status IN ('pending', 'processing', 'done', 'done_with_errors')",
        name="ck_cohorts_status",
    ),
)

students_t = Table(
    "students", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cohort_id", Integer, ForeignKey("cohorts.id"), nullable=False),
    Column("gdrive_file_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("recognized_name", String),
    Column("detected_variant", Integer),
    Column("status", String, nullable=False, server_default="pending"),
    Column("review_status", String),
    Column("error_message", String),
    Column("created_at", String, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint(
        "status IN ('pending', 'processing', 'processed', "
        "'requires_review', 'unreadable', 'error')",
        name="ck_students_status",
    ),
    CheckConstraint(
        "review_status IS NULL OR review_status IN ('pending', 'done')",
        name="ck_students_review",
    ),
)

task_results = Table(
    "task_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("student_id", Integer, ForeignKey("students.id"), nullable=False),
    Column("task_number", Integer, nullable=False),
    Column("page_number", Integer, nullable=False),
    Column("recognized_answer", String),
    Column("score", Float, nullable=False, server_default="0"),
    Column("max_score", Float, nullable=False),
    Column("confidence", String, nullable=False),
    Column("grading_notes", String),
    Column("manually_corrected", Integer, nullable=False, server_default="0"),
    Column("bbox", String),
    UniqueConstraint("student_id", "task_number", name="uq_task_results_student_task"),
    CheckConstraint("confidence IN ('low', 'high')", name="ck_task_results_confidence"),
)


# ---------------------------------------------------------------------------
# DB init + migrations
# ---------------------------------------------------------------------------

def _ensure_bbox_column(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(task_results)")).fetchall()
        col_names = {row[1] for row in cols}
        if "bbox" not in col_names:
            conn.execute(text("ALTER TABLE task_results ADD COLUMN bbox TEXT"))
            conn.commit()


def init_db() -> None:
    engine = get_engine()
    if engine.url.drivername.startswith("sqlite") and engine.url.database:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    metadata.create_all(engine)
    _ensure_bbox_column(engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dialect_insert(table_):
    engine = get_engine()
    if engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        raise NotImplementedError(f"Upsert не реализован для {engine.dialect.name}")
    return dialect_insert(table_)


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
    stmt = insert(cohorts).values(
        gdrive_folder_url=gdrive_folder_url,
        gdrive_folder_id=gdrive_folder_id,
        school=school, teacher=teacher,
        class_number=class_number, class_letter=class_letter,
        language=language, test_date=test_date,
        grade=grade, in_project=in_project,
    )
    with get_engine().begin() as conn:
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


def get_cohort(cohort_id: int):
    with get_engine().connect() as conn:
        return conn.execute(
            select(cohorts).where(cohorts.c.id == cohort_id)
        ).mappings().first()


def list_cohorts() -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(cohorts).order_by(cohorts.c.created_at.desc())
        ).mappings().all()


def get_next_pending_cohort():
    """Return the oldest pending cohort that is still in the project.

    Soft-deleted cohorts (in_project=0) are EXCLUDED — кнопка 🗑️ в Cohort
    Queue ставит in_project=0, и без этого фильтра обработчик всё равно
    бы их обрабатывал.
    """
    with get_engine().connect() as conn:
        return conn.execute(
            select(cohorts)
            .where(cohorts.c.status == "pending")
            .where(cohorts.c.in_project == 1)
            .order_by(cohorts.c.created_at.asc())
            .limit(1)
        ).mappings().first()


def update_cohort_status(cohort_id: int, status: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(status=status)
        )


def update_cohort_metadata(cohort_id: int, **fields) -> None:
    with get_engine().begin() as conn:
        row = conn.execute(
            select(cohorts.c.status).where(cohorts.c.id == cohort_id)
        ).mappings().first()
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
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(**fields)
        )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------

def create_student(cohort_id: int, gdrive_file_id: str, filename: str) -> int:
    stmt = insert(students_t).values(
        cohort_id=cohort_id, gdrive_file_id=gdrive_file_id, filename=filename,
    )
    with get_engine().begin() as conn:
        return conn.execute(stmt).inserted_primary_key[0]


def get_student(student_id: int):
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t).where(students_t.c.id == student_id)
        ).mappings().first()


def list_students_by_cohort(cohort_id: int) -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .where(students_t.c.cohort_id == cohort_id)
            .order_by(students_t.c.filename)
        ).mappings().all()


def update_student_status(student_id: int, status: str, error_message: str | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(status=status, error_message=error_message)
        )


def update_student_variant(student_id: int, detected_variant: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(detected_variant=detected_variant)
        )


def update_student_recognized_name(student_id: int, name: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(recognized_name=name)
        )


def list_requires_review() -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .where(students_t.c.status == "requires_review")
            .order_by(students_t.c.cohort_id, students_t.c.filename)
        ).mappings().all()


def set_review_pending(student_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(review_status="pending")
        )


def mark_student_reviewed(student_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(status="processed", review_status="done")
        )


def reset_failed_students_in_cohort(cohort_id: int) -> int:
    """Reset all students in a cohort whose status is 'error' back to 'pending'.

    Clears error_message too. Returns the number of rows updated.
    Used by the «🔄 Retry failed» button — куратор может перезапустить
    обработку студентов, которые упали (Drive timeout, LLM error, etc.)
    после того как причина устранена.
    """
    with get_engine().begin() as conn:
        res = conn.execute(
            update(students_t)
            .where(students_t.c.cohort_id == cohort_id)
            .where(students_t.c.status == "error")
            .values(status="pending", error_message=None)
        )
        # Also reset cohort status back to pending if it was done_with_errors
        conn.execute(
            update(cohorts)
            .where(cohorts.c.id == cohort_id)
            .where(cohorts.c.status.in_(("done", "done_with_errors")))
            .values(status="pending")
        )
        return res.rowcount or 0


def list_failed_students_in_cohort(cohort_id: int) -> list:
    """Return all students in this cohort with status='error' and their messages."""
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                select(students_t)
                .where(students_t.c.cohort_id == cohort_id)
                .where(students_t.c.status == "error")
                .order_by(students_t.c.id)
            ).mappings().all()
        )
def find_student_by_file(cohort_id: int, gdrive_file_id: str):
    """Найти студента в когорте по Drive-file-id. None если не найден.

    Используется для дедупликации при повторном Scan & Import — без этой
    проверки повторный импорт создаёт дубли для каждого PDF.
    """
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .where(students_t.c.cohort_id == cohort_id)
            .where(students_t.c.gdrive_file_id == gdrive_file_id)
            .limit(1)
        ).mappings().first()


def reactivate_cohort(cohort_id: int) -> None:
    """Вернуть soft-удалённую когорту обратно в проект (in_project=1).

    Используется в drive_walker.scan_and_import_root: если при повторном
    Scan находится существующая когорта с тем же ключом, но кто-то её ранее
    удалил кнопкой 🗑️, мы возвращаем её — пользователь явно повторно
    импортировал ту же папку, значит хочет её снова видеть.
    """
    with get_engine().begin() as conn:
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(in_project=1)
        )


def reset_student_for_reprocessing(student_id: int) -> None:
    """Полностью очистить студента, чтобы его прогнали через ИИ заново.

    Удаляет все task_results, сбрасывает status='pending', обнуляет
    error_message и review_status. detected_variant НЕ трогаем — куратор
    мог его вручную выставить, и мы хотим прогон именно с этим вариантом.

    Используется в Review Panel после ручного выбора варианта: «Применить
    вариант и переотправить ученика на проверку».
    """
    with get_engine().begin() as conn:
        conn.execute(
            task_results.delete().where(task_results.c.student_id == student_id)
        )
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(status="pending", error_message=None, review_status=None)
        )


def clear_student_error(student_id: int) -> None:
    """Обнулить error_message студента (после успешного retry)."""
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(error_message=None)
        )




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
    bbox: str | None = None,
) -> None:
    stmt = _dialect_insert(task_results).values(
        student_id=student_id, task_number=task_number,
        page_number=page_number, recognized_answer=recognized_answer,
        score=score, max_score=max_score, confidence=confidence,
        grading_notes=grading_notes, bbox=bbox,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["student_id", "task_number"],
        set_=dict(
            page_number=stmt.excluded.page_number,
            recognized_answer=stmt.excluded.recognized_answer,
            score=stmt.excluded.score,
            max_score=stmt.excluded.max_score,
            confidence=stmt.excluded.confidence,
            grading_notes=stmt.excluded.grading_notes,
            bbox=stmt.excluded.bbox,
        ),
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def get_task_results(student_id: int) -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(task_results)
            .where(task_results.c.student_id == student_id)
            .order_by(task_results.c.task_number)
        ).mappings().all()


def update_task_result(
    result_id: int,
    recognized_answer: str,
    score: float,
    manually_corrected: bool = True,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(task_results)
            .where(task_results.c.id == result_id)
            .values(
                recognized_answer=recognized_answer,
                score=score,
                manually_corrected=int(manually_corrected),
            )
        )
