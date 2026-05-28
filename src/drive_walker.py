"""Recursive walk of the root Drive folder + automatic cohort creation (v5 / fixes20).

Что изменилось по сравнению с v4 (fixes15):
- Полностью удалена функциональность перевода ФИО на русский. Имена
  сохраняются в БД ровно так, как написано в имени PDF-файла
  («Abbaszadə Nəzrin» останется «Abbaszadə Nəzrin»). Это упрощает код,
  убирает лишний LLM-вызов и связанную с ним нагрузку.
- Параметр `skip_translation`, поле `names_translated` в ImportSummary и
  фаза `translating` в progress_callback — удалены.

Иерархия (без изменений):
    Root
    ├── Аз сектор / Ру сектор          → language
    │   ├── 2 класс / 3 класс          → grade
    │   │   ├── <teacher folder>       → school + teacher + in_project
    │   │   │   ├── <Student Name Nb>.pdf   → student_name + class
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator, Optional

import src.db as db
import src.drive as drive


# --- parsers ---------------------------------------------------------------

_SECTOR_MAP = {
    "ру": "ru", "ru": "ru", "русский": "ru", "русский сектор": "ru",
    "аз": "az", "az": "az", "азербайджанский": "az", "азербайджанский сектор": "az",
}


def parse_sector_folder(name: str) -> str:
    s = (name or "").strip().lower()
    s_clean = re.sub(r"\s*сектор\s*", "", s).strip()
    if s_clean in _SECTOR_MAP:
        return _SECTOR_MAP[s_clean]
    if s in _SECTOR_MAP:
        return _SECTOR_MAP[s]
    raise ValueError(f"Cannot parse sector folder name: {name!r}.")


_GRADE_RE = re.compile(r"^\s*([23])\s*(?:класс)?\s*$", re.IGNORECASE)


def parse_grade_folder(name: str) -> int:
    m = _GRADE_RE.match(name or "")
    if not m:
        raise ValueError(f"Cannot parse grade folder name: {name!r}.")
    return int(m.group(1))


_DASH_CHARS = "‐‑‒–—―−"
_DASH_TRANS = str.maketrans({c: "-" for c in _DASH_CHARS})
_PARENS_STATUS_RE = re.compile(r"\(([^)]+)\)\s*$")


def parse_teacher_folder(name: str) -> tuple[str, str, Optional[bool]]:
    raw = (name or "").strip().translate(_DASH_TRANS)
    in_project: Optional[bool] = None
    m_status = _PARENS_STATUS_RE.search(raw)
    if m_status:
        st = m_status.group(1).strip().lower()
        if st == "в проекте":
            in_project = True
        elif st == "не в проекте":
            in_project = False
        raw = raw[: m_status.start()].strip()
    parts = re.split(r"\s+-\s+", raw, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(f"Cannot parse teacher folder name: {name!r}.")
    return parts[0].strip(), parts[1].strip(), in_project


_STUDENT_FILENAME_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<num>\d+)(?P<letter>\S+?)\s*\.pdf\s*$",
    re.IGNORECASE,
)


def parse_student_filename(filename: str) -> tuple[str, int, str]:
    raw = (filename or "").strip()
    m = _STUDENT_FILENAME_RE.match(raw)
    if not m:
        raise ValueError(f"Cannot parse student PDF filename: {filename!r}.")
    name = m.group("name").strip()
    num = int(m.group("num"))
    letter = m.group("letter").rstrip(".").strip()
    if not letter:
        raise ValueError(f"Cannot parse class letter from filename: {filename!r}.")
    return name, num, letter


# --- discovery -------------------------------------------------------------


@dataclass
class TeacherEntry:
    sector_folder_id: str
    sector_folder_name: str
    language: str
    grade_folder_id: str
    grade_folder_name: str
    grade: int
    teacher_folder_id: str
    teacher_folder_name: str
    school: str
    teacher: str
    in_project: Optional[bool] = None


def discover_options_shallow(service, root_folder_id: str) -> list[TeacherEntry]:
    """Walk down to teacher folders only (skip PDFs). Cheap pre-Import discovery."""
    entries: list[TeacherEntry] = []
    for sector in drive.list_subfolders(service, root_folder_id):
        try:
            language = parse_sector_folder(sector["name"])
        except ValueError:
            continue
        for grade_f in drive.list_subfolders(service, sector["id"]):
            try:
                grade = parse_grade_folder(grade_f["name"])
            except ValueError:
                continue
            for teacher_f in drive.list_subfolders(service, grade_f["id"]):
                try:
                    school, teacher, in_project = parse_teacher_folder(teacher_f["name"])
                except ValueError:
                    continue
                entries.append(TeacherEntry(
                    sector_folder_id=sector["id"], sector_folder_name=sector["name"],
                    language=language,
                    grade_folder_id=grade_f["id"], grade_folder_name=grade_f["name"],
                    grade=grade,
                    teacher_folder_id=teacher_f["id"], teacher_folder_name=teacher_f["name"],
                    school=school, teacher=teacher, in_project=in_project,
                ))
    return entries


def options_summary(entries: list[TeacherEntry]) -> dict:
    return {
        "sectors": sorted({e.language for e in entries}),
        "grades": sorted({e.grade for e in entries}),
        "schools": sorted({e.school for e in entries}),
        "teachers": sorted({e.teacher for e in entries}),
    }


# --- walk ------------------------------------------------------------------


@dataclass
class WalkResult:
    sector_folder: dict
    grade_folder: Optional[dict] = None
    teacher_folder: Optional[dict] = None
    pdf_file: Optional[dict] = None

    language: Optional[str] = None
    grade: Optional[int] = None
    school: Optional[str] = None
    teacher: Optional[str] = None
    in_project: Optional[bool] = None
    student_name: Optional[str] = None
    class_number: Optional[int] = None
    class_letter: Optional[str] = None

    error: Optional[str] = None
    error_at: Optional[str] = None


def _passes_filter(value, filter_value):
    if filter_value in (None, "", "All"):
        return True
    return value == filter_value


def walk_root(
    service,
    root_folder_id: str,
    filters: Optional[dict] = None,
) -> Iterator[WalkResult]:
    filters = filters or {}
    sector_f = filters.get("sector")
    grade_f = filters.get("grade")
    school_f = filters.get("school")
    teacher_f = filters.get("teacher")

    for sector in drive.list_subfolders(service, root_folder_id):
        try:
            language = parse_sector_folder(sector["name"])
        except ValueError as exc:
            yield WalkResult(sector_folder=sector, error=str(exc), error_at="sector")
            continue
        if not _passes_filter(language, sector_f):
            continue

        for grade_folder in drive.list_subfolders(service, sector["id"]):
            try:
                grade = parse_grade_folder(grade_folder["name"])
            except ValueError as exc:
                yield WalkResult(
                    sector_folder=sector, grade_folder=grade_folder,
                    language=language, error=str(exc), error_at="grade",
                )
                continue
            if not _passes_filter(grade, grade_f):
                continue

            for teacher_folder in drive.list_subfolders(service, grade_folder["id"]):
                try:
                    school, teacher, in_project = parse_teacher_folder(teacher_folder["name"])
                except ValueError as exc:
                    yield WalkResult(
                        sector_folder=sector, grade_folder=grade_folder,
                        teacher_folder=teacher_folder,
                        language=language, grade=grade,
                        error=str(exc), error_at="teacher",
                    )
                    continue
                if not _passes_filter(school, school_f):
                    continue
                if not _passes_filter(teacher, teacher_f):
                    continue

                for pdf in drive.list_pdfs(service, teacher_folder["id"]):
                    base = dict(
                        sector_folder=sector, grade_folder=grade_folder,
                        teacher_folder=teacher_folder, pdf_file=pdf,
                        language=language, grade=grade,
                        school=school, teacher=teacher, in_project=in_project,
                    )
                    try:
                        student_name, class_num, class_letter = parse_student_filename(pdf["name"])
                    except ValueError as exc:
                        yield WalkResult(error=str(exc), error_at="pdf", **base)
                        continue
                    yield WalkResult(
                        student_name=student_name,
                        class_number=class_num,
                        class_letter=class_letter,
                        **base,
                    )


# --- import ----------------------------------------------------------------


@dataclass
class ImportSummary:
    cohorts_created: int = 0
    students_created: int = 0
    students_skipped_dupes: int = 0
    students_flagged_for_review: int = 0
    errors: list[str] = field(default_factory=list)


def _cohort_key(r: WalkResult) -> tuple:
    return (r.school, r.teacher, r.class_number, r.class_letter,
            r.language, r.grade)


def _folder_url(folder: Optional[dict]) -> str:
    return f"https://drive.google.com/drive/folders/{folder['id']}" if folder else ""


ProgressCallback = Callable[[str, int, int], None]


def scan_and_import_root(
    service,
    root_folder_id: str,
    root_folder_url: str,
    test_date: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    filters: Optional[dict] = None,
) -> ImportSummary:
    """Walk the tree (with optional filters), create cohorts/students.

    DEDUPLICATION:
    - Cohorts: per (school, teacher, class_number, class_letter, language, grade).
    - Students: per (cohort_id, gdrive_file_id).

    Имена сохраняются как есть из имени PDF-файла. Никакой транслитерации
    больше нет.
    """
    if test_date is None:
        test_date = date.today().isoformat()

    def _progress(stage: str, current: int, total: int) -> None:
        if progress_callback is not None:
            try: progress_callback(stage, current, total)
            except Exception: pass

    summary = ImportSummary()

    # Phase 1: walk
    _progress("walking", 0, 0)
    results: list[WalkResult] = []
    for r in walk_root(service, root_folder_id, filters=filters):
        results.append(r)
        _progress("walking", len(results), len(results))

    # Phase 2: write to DB
    cohort_cache: dict[tuple, int] = {}
    existing: dict[tuple, int] = {}
    for c in db.list_cohorts():
        existing[(
            c["school"], c["teacher"], c["class_number"], c["class_letter"],
            c["language"], c["grade"],
        )] = c["id"]

    reactivated_ids: set[int] = set()

    def _ensure_active(cid: int) -> None:
        if cid in reactivated_ids:
            return
        reactivated_ids.add(cid)
        try:
            db.reactivate_cohort(cid)
        except AttributeError:
            pass

    def _get_or_create_cohort(result: WalkResult, class_number: int, class_letter: str) -> int:
        key = (
            result.school, result.teacher, class_number, class_letter,
            result.language, result.grade,
        )
        cohort_id = cohort_cache.get(key) or existing.get(key)
        if cohort_id is None:
            cohort_id = db.create_cohort(
                gdrive_folder_url=_folder_url(result.teacher_folder),
                gdrive_folder_id=result.teacher_folder["id"],
                school=result.school, teacher=result.teacher,
                class_number=class_number, class_letter=class_letter,
                language=result.language, test_date=test_date, grade=result.grade,
                in_project=1 if result.in_project is None else int(bool(result.in_project)),
            )
            summary.cohorts_created += 1
        else:
            _ensure_active(cohort_id)
        cohort_cache[key] = cohort_id
        return cohort_id

    def _create_student_if_new(cohort_id: int, pdf: dict) -> Optional[int]:
        try:
            existing_student = db.find_student_by_file(cohort_id, pdf["id"])
        except AttributeError:
            existing_student = None
        if existing_student is not None:
            summary.students_skipped_dupes += 1
            return None
        return db.create_student(cohort_id, pdf["id"], pdf["name"])

    for i, result in enumerate(results):
        _progress("saving", i, len(results))

        if result.error and result.error_at in ("sector", "grade", "teacher"):
            path_parts = []
            for f in (result.sector_folder, result.grade_folder, result.teacher_folder):
                if f:
                    path_parts.append(f.get("name", "?"))
            summary.errors.append(
                f"[{result.error_at}] {' / '.join(path_parts) or '(no path)'}: {result.error}"
            )
            continue

        if result.error and result.error_at == "pdf":
            cohort_id = _get_or_create_cohort(result, result.grade, "?")
            student_id = _create_student_if_new(cohort_id, result.pdf_file)
            if student_id is not None:
                db.update_student_status(student_id, "requires_review", error_message=result.error)
                try: db.set_review_pending(student_id)
                except AttributeError: pass
                summary.students_created += 1
                summary.students_flagged_for_review += 1
            summary.errors.append(f"[pdf] {result.pdf_file.get('name')}: {result.error}")
            continue

        # Happy path
        cohort_id = _get_or_create_cohort(result, result.class_number, result.class_letter)
        student_id = _create_student_if_new(cohort_id, result.pdf_file)
        if student_id is None:
            continue

        if result.student_name:
            try:
                db.update_student_recognized_name(student_id, result.student_name)
            except AttributeError:
                pass
        summary.students_created += 1

    _progress("saving", len(results), len(results))
    return summary
