"""SQLAlchemy-based database layer for math_checker — v4 (stage16).

stage16 — ОПТИМИЗАЦИЯ для большой БД (68 когорт / 1300+ студентов):
- Новая функция `get_cohort_overview_for_user` — один SQL-запрос с
  JOIN cohorts × students + агрегация (COUNT по статусам). Раньше для
  каждой когорты делалось 3 отдельных запроса (list_students_by_cohort
  + count_unfinished_in_cohort + list_failed_students_in_cohort) =
  N × 3 = 200+ запросов в БД на каждый рендер главной страницы.

Все старые функции сохранены — другие страницы / скрипты не ломаются.
"""
import os
import sqlite3
from pathlib import Path

from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Float, ForeignKey, DateTime, text, inspect,
    CheckConstraint, UniqueConstraint, func, case, and_, or_,
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
    def __init__(self, engine):
        self._engine = engine
        self._raw = None

    def __enter__(self):
        self._raw = self._engine.raw_connection()
        if self._engine.dialect.name == "sqlite":
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
    Column("owner_user_id", Integer, ForeignKey("users.id"), nullable=True),
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
    Column("rotate_180", Integer, nullable=False, server_default="0"),
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
    insp = inspect(engine)
    if not insp.has_table("users"):
        users_table.create(engine)


def _ensure_reviewed_columns(engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("students"):
        return
    existing = {col["name"] for col in insp.get_columns("students")}
    with engine.begin() as conn:
        if "reviewed_by" not in existing:
            conn.execute(text("ALTER TABLE students ADD COLUMN reviewed_by TEXT"))
        if "reviewed_at" not in existing:
            conn.execute(text("ALTER TABLE students ADD COLUMN reviewed_at TIMESTAMP"))


def _ensure_owner_column(engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("cohorts"):
        return
    existing = {col["name"] for col in insp.get_columns("cohorts")}
    if "owner_user_id" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE cohorts ADD COLUMN owner_user_id INTEGER"))


def _ensure_rotate_180_column(engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("students"):
        return
    existing = {col["name"] for col in insp.get_columns("students")}
    if "rotate_180" not in existing:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE students ADD COLUMN rotate_180 INTEGER NOT NULL DEFAULT 0"
            ))


def _ensure_indexes(engine) -> None:
    """stage16: индексы для быстрых выборок.

    students.cohort_id — самый частый JOIN/фильтр.
    students.status — часто фильтруем по статусу.
    cohorts.owner_user_id — для изоляции кураторов.
    """
    dialect = engine.dialect.name
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_students_cohort_id ON students (cohort_id)",
        "CREATE INDEX IF NOT EXISTS ix_students_status ON students (status)",
        "CREATE INDEX IF NOT EXISTS ix_cohorts_owner ON cohorts (owner_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_task_results_student ON task_results (student_id)",
    ]
    with engine.begin() as conn:
        for s in statements:
            try:
                conn.execute(text(s))
            except Exception:
                # некоторые диалекты могут не поддерживать IF NOT EXISTS — пропускаем тихо
                pass


def init_db() -> None:
    engine = get_engine()
    if engine.url.drivername.startswith("sqlite") and engine.url.database:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    metadata.create_all(engine)
    _ensure_bbox_column(engine)
    _ensure_users_table(engine)
    _ensure_reviewed_columns(engine)
    _ensure_owner_column(engine)
    _ensure_rotate_180_column(engine)
    _ensure_indexes(engine)


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


def _cohort_visibility_filter(user_id: int | None, role: str | None):
    if role == "admin" or user_id is None:
        return None
    return or_(
        cohorts.c.owner_user_id == user_id,
        cohorts.c.owner_user_id.is_(None),
    )


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
    owner_user_id: int | None = None,
) -> int:
    stmt = insert(cohorts).values(
        gdrive_folder_url=gdrive_folder_url,
        gdrive_folder_id=gdrive_folder_id,
        school=school, teacher=teacher,
        class_number=class_number, class_letter=class_letter,
        language=language, test_date=test_date,
        grade=grade, in_project=in_project,
        owner_user_id=owner_user_id,
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


def list_cohorts_for_user(user_id: int | None, role: str | None) -> list:
    stmt = select(cohorts).order_by(cohorts.c.created_at.desc())
    flt = _cohort_visibility_filter(user_id, role)
    if flt is not None:
        stmt = stmt.where(flt)
    with get_engine().connect() as conn:
        return conn.execute(stmt).mappings().all()


def get_cohort_overview_for_user(user_id: int | None, role: str | None) -> list[dict]:
    """ONE QUERY: вернуть когорты + агрегаты по студентам.

    Для каждой когорты:
      - total: всего студентов;
      - processed_or_terminal: со статусом processed / requires_review /
        unreadable / error (= «больше не в очереди»);
      - n_unfinished: pending / processing / requires_review-с-error;
      - n_failed: requires_review с error_message (для отдельного списка
        «работы с проблемами» в expander).

    Это заменяет N × {list_students_by_cohort, count_unfinished_in_cohort,
    list_failed_students_in_cohort} = N × 3 = 204 запроса при 68 когортах.
    """
    is_terminal = students_t.c.status.in_(
        ("processed", "requires_review", "unreadable", "error")
    )
    is_unfinished = or_(
        students_t.c.status.in_(("error", "processing", "pending")),
        and_(
            students_t.c.status == "requires_review",
            students_t.c.error_message.isnot(None),
        ),
    )
    is_failed = or_(
        students_t.c.status.in_(("error", "processing")),
        and_(
            students_t.c.status == "requires_review",
            students_t.c.error_message.isnot(None),
        ),
    )

    overview_stmt = (
        select(
            cohorts.c.id.label("id"),
            cohorts.c.created_at,
            cohorts.c.gdrive_folder_url, cohorts.c.gdrive_folder_id,
            cohorts.c.school, cohorts.c.teacher,
            cohorts.c.class_number, cohorts.c.class_letter,
            cohorts.c.in_project, cohorts.c.language,
            cohorts.c.test_date, cohorts.c.grade,
            cohorts.c.status, cohorts.c.owner_user_id,
            func.count(students_t.c.id).label("total"),
            func.sum(case((is_terminal, 1), else_=0)).label("processed_or_terminal"),
            func.sum(case((is_unfinished, 1), else_=0)).label("n_unfinished"),
            func.sum(case((is_failed, 1), else_=0)).label("n_failed"),
        )
        .select_from(cohorts.outerjoin(students_t, students_t.c.cohort_id == cohorts.c.id))
        .group_by(cohorts.c.id)
        .order_by(cohorts.c.created_at.desc())
    )

    flt = _cohort_visibility_filter(user_id, role)
    if flt is not None:
        overview_stmt = overview_stmt.where(flt)

    with get_engine().connect() as conn:
        rows = conn.execute(overview_stmt).mappings().all()

    return [
        {
            **dict(r),
            "total": int(r["total"] or 0),
            "processed_or_terminal": int(r["processed_or_terminal"] or 0),
            "n_unfinished": int(r["n_unfinished"] or 0),
            "n_failed": int(r["n_failed"] or 0),
        }
        for r in rows
    ]


def get_next_pending_cohort():
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
                   "test_date", "grade", "gdrive_folder_url", "gdrive_folder_id",
                   "in_project", "owner_user_id"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unknown cohort fields: {invalid}")
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(**fields)
        )


def set_cohort_owner(cohort_id: int, owner_user_id: int | None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(
                owner_user_id=owner_user_id,
            )
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


def update_student_rotation(student_id: int, rotate_180: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(students_t)
            .where(students_t.c.id == student_id)
            .values(rotate_180=1 if rotate_180 else 0)
        )


def list_requires_review() -> list:
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .where(students_t.c.status == "requires_review")
            .order_by(students_t.c.cohort_id, students_t.c.filename)
        ).mappings().all()


def list_requires_review_for_user(user_id: int | None, role: str | None) -> list:
    flt = _cohort_visibility_filter(user_id, role)
    if flt is None:
        return list_requires_review()
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .select_from(students_t.join(cohorts, students_t.c.cohort_id == cohorts.c.id))
            .where(students_t.c.status == "requires_review")
            .where(flt)
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


def reset_failed_students_in_cohort(cohort_id: int) -> int:
    return reset_unfinished_students_in_cohort(cohort_id)


def list_failed_students_in_cohort(cohort_id: int) -> list:
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
    with get_engine().connect() as conn:
        return conn.execute(
            select(students_t)
            .where(students_t.c.cohort_id == cohort_id)
            .where(students_t.c.gdrive_file_id == gdrive_file_id)
            .limit(1)
        ).mappings().first()


def reactivate_cohort(cohort_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(cohorts).where(cohorts.c.id == cohort_id).values(in_project=1)
        )


def reset_student_for_reprocessing(student_id: int) -> None:
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


def get_review_stats(date_from: str | None = None, date_to: str | None = None,
                     user_id: int | None = None, role: str | None = None) -> list[dict]:
    from sqlalchemy import distinct
    conds = [students_t.c.reviewed_by.isnot(None)]
    if date_from:
        conds.append(students_t.c.reviewed_at >= f"{date_from} 00:00:00")
    if date_to:
        conds.append(students_t.c.reviewed_at <= f"{date_to} 23:59:59")

    use_join = (role != "admin" and user_id is not None)

    with get_engine().connect() as conn:
        sl_select_from = students_t.outerjoin(
            task_results, task_results.c.student_id == students_t.c.id
        )
        if use_join:
            sl_select_from = sl_select_from.join(
                cohorts, students_t.c.cohort_id == cohorts.c.id
            )

        student_level = (
            select(
                students_t.c.reviewed_by.label("reviewed_by"),
                students_t.c.id.label("sid"),
                students_t.c.reviewed_at.label("reviewed_at"),
                func.coalesce(func.max(task_results.c.manually_corrected), 0).label("is_manual"),
            )
            .select_from(sl_select_from)
            .where(*conds)
        )
        if use_join:
            student_level = student_level.where(
                or_(
                    cohorts.c.owner_user_id == user_id,
                    cohorts.c.owner_user_id.is_(None),
                )
            )
        student_level = student_level.group_by(
            students_t.c.id, students_t.c.reviewed_by, students_t.c.reviewed_at
        ).subquery()

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
