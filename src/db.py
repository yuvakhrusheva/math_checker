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
    MetaData, Table, Column, Integer, String, Float, ForeignKey, DateTime, text, inspect,
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
    Column("reviewed_by", String),
    Column("reviewed_at", DateTime),
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


users_table = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String, nullable=False, unique=True),
    Column("display_name", String, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("role", String, nullable=False, server_default="curator"),
    Column("email", String, nullable=True),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("role IN ('curator', 'admin')",
                    name="ck_users_role"),
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



def _ensure_users_table(engine) -> None:
    """Idempotent migration: create users table if missing."""
    insp = inspect(engine)
    if not insp.has_table("users"):
        users_table.create(engine)


def _ensure_reviewed_columns(engine) -> None:
    """Idempotent migration: add reviewed_by / reviewed_at columns to students."""
    insp = inspect(engine)
    if not insp.has_table("students"):
        return
    existing = {col["name"] for col in insp.get_columns("students")}
    with engine.begin() as conn:
        if "reviewed_by" not in existing:
            conn.execute(text("ALTER TABLE students ADD COLUMN reviewed_by TEXT"))
        if "reviewed_at" not in existing:
            conn.execute(text("ALTER TABLE students ADD COLUMN reviewed_at TIMESTAMP"))

def init_db() -> None:
    engine = get_engine()
    if engine.url.drivername.startswith("sqlite") and engine.url.database:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    metadata.create_all(engine)
    _ensure_bbox_column(engine)
    _ensure_users_table(engine)
    _ensure_reviewed_columns(engine)


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


def mark_student_reviewed(student_id: int, reviewed_by: str | None = None) -> None:
    """Mark student as done with review.

    Sets status='processed', review_status='done', reviewed_by, reviewed_at.
    reviewed_by — логин куратора (для статистики «кто сколько сделал»).
    """
    from datetime import datetime as _dt
    with get_engine().begin() as conn:
        values = {
            "status": "processed",
            "review_status": "done",
            "reviewed_at": _dt.utcnow(),
        }
        if reviewed_by:
            values["reviewed_by"] = reviewed_by
        conn.execute(
            update(students_t).where(students_t.c.id == student_id).values(**values)
        )


def reset_unfinished_students_in_cohort(cohort_id: int) -> int:
    """Reset NON-finished students in a cohort back to 'pending' for reprocessing.

    Сбрасываются:
      - status='error'      — упавшие (старый статус, на всякий случай);
      - status='processing' — застрявшие (обработка прервана на середине);
      - status='requires_review' С error_message — упавшие, которые fixes13
        переводит в review с сообщением об ошибке (LLM JSON error, SSL, и т.п.).

    НЕ трогаются:
      - status='processed'  — успешно обработано;
      - status='requires_review' БЕЗ error_message — нормальная ручная проверка
        (вариант/визуальная задача), её должен сделать куратор;
      - status='unreadable' — битый PDF (повторная обработка не поможет).

    Возвращает число сброшенных. Также возвращает когорту в 'pending', если
    она была done / done_with_errors.
    """
    from sqlalchemy import or_, and_
    with get_engine().begin() as conn:
        res = conn.execute(
            update(students_t)
            .where(students_t.c.cohort_id == cohort_id)
            .where(
                or_(
                    students_t.c.status.in_(("error", "processing")),
                    and_(
                        students_t.c.status == "requires_review",
                        students_t.c.error_message.isnot(None),
                    ),
                )
            )
            .values(status="pending", error_message=None, review_status=None)
        )
        conn.execute(
            update(cohorts)
            .where(cohorts.c.id == cohort_id)
            .where(cohorts.c.status.in_(("done", "done_with_errors")))
            .values(status="pending")
        )
        return res.rowcount or 0


# Обратная совместимость со старым именем (fixes12 UI).
def reset_failed_students_in_cohort(cohort_id: int) -> int:
    return reset_unfinished_students_in_cohort(cohort_id)


def list_failed_students_in_cohort(cohort_id: int) -> list:
    """Студенты когорты, которые НЕ обработались как надо (есть проблема).

    Включает: status='error', status='processing' (застрявшие), и
    status='requires_review' С error_message (упавшие, переведённые в review).
    """
    from sqlalchemy import or_, and_
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                select(students_t)
                .where(students_t.c.cohort_id == cohort_id)
                .where(
                    or_(
                        students_t.c.status.in_(("error", "processing")),
                        and_(
                            students_t.c.status == "requires_review",
                            students_t.c.error_message.isnot(None),
                        ),
                    )
                )
                .order_by(students_t.c.id)
            ).mappings().all()
        )


def count_unfinished_in_cohort(cohort_id: int) -> int:
    """Сколько в когорте незавершённых работ (error/processing/pending/req-review-с-ошибкой)."""
    from sqlalchemy import or_, and_, func
    with get_engine().connect() as conn:
        row = conn.execute(
            select(func.count(students_t.c.id))
            .where(students_t.c.cohort_id == cohort_id)
            .where(
                or_(
                    students_t.c.status.in_(("error", "processing", "pending")),
                    and_(
                        students_t.c.status == "requires_review",
                        students_t.c.error_message.isnot(None),
                    ),
                )
            )
        ).scalar()
    return int(row or 0)
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


def set_student_reviewer(student_id: int, reviewed_by: str) -> None:
    """Проставить reviewed_by/reviewed_at студенту (без смены статуса).

    Используется queue_processor для авто-обработанных (processed) работ —
    засчитать их куратору, запустившему обработку. НЕ перезаписывает, если
    студента уже отметил кто-то вручную? — перезаписывает: последний, кто
    «коснулся» работы, и есть текущий ответственный.
    """
    if not reviewed_by:
        return
    from datetime import datetime as _dt
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(reviewed_by=reviewed_by, reviewed_at=_dt.utcnow())
        )


def stamp_reviewer_by_result(result_id: int, reviewed_by: str) -> None:
    """По id задачи (task_results.id) найти студента и проставить reviewed_by/at."""
    if not reviewed_by:
        return
    from datetime import datetime as _dt
    with get_engine().begin() as conn:
        row = conn.execute(
            select(task_results.c.student_id).where(task_results.c.id == result_id)
        ).mappings().first()
        if not row:
            return
        conn.execute(
            update(students_t)
            .where(students_t.c.id == row["student_id"])
            .values(reviewed_by=reviewed_by, reviewed_at=_dt.utcnow())
        )


def get_review_stats(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Статистика «кто сколько проверил» с разбивкой по типу проверки.

    Для каждого куратора (students.reviewed_by) считаем:
      - total_reviewed   — всего работ, отмеченных куратором;
      - manual_reviewed  — из них с РУЧНЫМИ правками баллов
                           (есть task_results.manually_corrected=1);
      - ai_accepted      — принято без правок (ИИ-оценка подтверждена) =
                           total - manual;
      - last_review      — дата последней проверки.

    Опционально фильтрует по reviewed_at (строки 'YYYY-MM-DD').
    """
    from sqlalchemy import func, case, distinct
    conds = [students_t.c.reviewed_by.isnot(None)]
    if date_from:
        conds.append(students_t.c.reviewed_at >= f"{date_from} 00:00:00")
    if date_to:
        conds.append(students_t.c.reviewed_at <= f"{date_to} 23:59:59")

    # manual = студент, у которого ХОТЯ БЫ одна задача manually_corrected=1
    manual_case = case(
        (func.max(task_results.c.manually_corrected) == 1, 1),
        else_=0,
    )

    with get_engine().connect() as conn:
        # Сначала на уровне студента определим manual/auto, потом сгруппируем.
        student_level = (
            select(
                students_t.c.reviewed_by.label("reviewed_by"),
                students_t.c.id.label("sid"),
                students_t.c.reviewed_at.label("reviewed_at"),
                func.coalesce(func.max(task_results.c.manually_corrected), 0).label("is_manual"),
            )
            .select_from(
                students_t.outerjoin(
                    task_results, task_results.c.student_id == students_t.c.id
                )
            )
            .where(*conds)
            .group_by(students_t.c.id, students_t.c.reviewed_by, students_t.c.reviewed_at)
            .subquery()
        )

        rows = conn.execute(
            select(
                student_level.c.reviewed_by,
                func.count(student_level.c.sid).label("total_reviewed"),
                func.sum(student_level.c.is_manual).label("manual_reviewed"),
                func.max(student_level.c.reviewed_at).label("last_review"),
            )
            .group_by(student_level.c.reviewed_by)
            .order_by(func.count(student_level.c.sid).desc())
        ).mappings().all()

    result = []
    for r in rows:
        total = int(r["total_reviewed"] or 0)
        manual = int(r["manual_reviewed"] or 0)
        result.append({
            "reviewed_by": r["reviewed_by"],
            "total_reviewed": total,
            "manual_reviewed": manual,
            "ai_accepted": total - manual,
            "last_review": r["last_review"],
        })
    return result


def get_reviewed_students(date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Список проверенных студентов (для детализации / графика по дням)."""
    conds = [students_t.c.reviewed_by.isnot(None)]
    if date_from:
        conds.append(students_t.c.reviewed_at >= f"{date_from} 00:00:00")
    if date_to:
        conds.append(students_t.c.reviewed_at <= f"{date_to} 23:59:59")
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                students_t.c.id,
                students_t.c.reviewed_by,
                students_t.c.reviewed_at,
                students_t.c.cohort_id,
            ).where(*conds)
        ).mappings().all()
    return [dict(r) for r in rows]


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


def save_manual_task_result(
    student_id: int,
    task_number: int,
    page_number: int,
    score: float,
    max_score: float,
    reviewed_by: str | None = None,
    recognized_answer: str = "(manual)",
) -> None:
    """Upsert ручной проверки задачи.

    Ставит confidence='high', manually_corrected=1. recognized_answer —
    ответ ученика, который ввёл куратор (по нему ИИ уже посчитал балл).
    Используется при ручной проверке (в т.ч. упавших работ).
    """
    stmt = _dialect_insert(task_results).values(
        student_id=student_id, task_number=task_number,
        page_number=page_number, recognized_answer=recognized_answer,
        score=score, max_score=max_score, confidence="high",
        grading_notes="Manually graded by curator", bbox=None,
        manually_corrected=1,
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
            manually_corrected=stmt.excluded.manually_corrected,
        ),
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)
    if reviewed_by:
        set_student_reviewer(student_id, reviewed_by)


def get_task_results(student_id: int) -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(task_results)
            .where(task_results.c.student_id == student_id)
            .order_by(task_results.c.task_number)
        ).mappings().all()


def _stamp_student_reviewer(student_id, reviewed_by):
    """Helper: stamp who manually edited a task result (for curator stats)."""
    if not reviewed_by:
        return
    from datetime import datetime as _dt
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t).where(students_t.c.id == student_id).values(
                reviewed_by=reviewed_by, reviewed_at=_dt.utcnow(),
            )
        )


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
