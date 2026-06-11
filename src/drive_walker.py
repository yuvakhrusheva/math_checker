"""Recursive walk of the root Drive folder + automatic cohort creation — v7 (stage18).

stage18 — гибкий парсер имён файлов:
- Принимает варианты: «Имя 2C.pdf», «Имя 2 C.pdf», «Имя 2-C.pdf»,
  «Имя 2_c.pdf» (с пробелом / дефисом / подчёркиванием между цифрой и
  буквой класса, любой регистр).
- Если в имени НЕТ цифры+буквы (только «Имя.pdf») — fallback: берём
  grade из вложенной папки `grade_folder`, class_letter = "X" (маркер
  «класс неизвестен», заметный куратору в Cohort Queue).
- Снимает «(N)»-суффикс копий Drive — как в stage17.
- Сохраняется backward-compat: parse_student_filename без второго
  аргумента работает как раньше (raise ValueError на пустом случае).

Логика walk_root тоже подкручена: если class_number/class_letter не
распознаны, подставляются дефолты из иерархии.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
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


# stage17: убрать " (N)" суффикс копии Drive.
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)(?=\s*\.pdf\s*$)", re.IGNORECASE)

# stage18: гибкий полный матч — имя + цифра + опц. разделитель + буква.
_STUDENT_FILENAME_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<num>\d+)[\s\-_]*(?P<letter>[A-Za-zА-Яа-я])"
    r"[A-Za-zА-Яа-я0-9]*\s*\.pdf\s*$",
    re.IGNORECASE,
)

# stage18: fallback — просто «Имя Фамилия.pdf» без класса в имени.
_NAME_ONLY_RE = re.compile(r"^(?P<name>.+?)\s*\.pdf\s*$", re.IGNORECASE)


# Маркер «класс из имени файла не извлечён». Используется как class_letter,
# когда в имени нет «2C»-маркера. Куратор видит когорту «2X» / «3X» и
# понимает, что нужно проверить и при желании переименовать класс.
UNKNOWN_CLASS_LETTER = "X"


def parse_student_filename(
    filename: str,
    default_class_number: int | None = None,
    default_class_letter: str | None = None,
) -> tuple[str, int, str]:
    """Распарсить имя PDF-файла студента.

    Принимает варианты:
      - «Имя Фамилия 2C.pdf» — стандарт
      - «Имя Фамилия 2 c.pdf» / «Имя 2-С.pdf» / «Имя 2_c.pdf» — пробел/дефис/подчёрк.
      - «Имя Фамилия.pdf» — только при заданных default_class_number/letter
        (берётся из вложенной папки grade_folder).

    Без default — поведение как раньше: raise ValueError, если класс
    в имени не виден.
    """
    raw = (filename or "").strip()
    # Снять " (N)" суффикс копии.
    raw = _COPY_SUFFIX_RE.sub("", raw)

    m = _STUDENT_FILENAME_RE.match(raw)
    if m:
        name = m.group("name").strip()
        num = int(m.group("num"))
        letter = m.group("letter").upper().strip()
        if name and letter:
            return name, num, letter

    # Fallback: имя без класса. Допускаем, только если есть дефолт.
    if default_class_number is not None and default_class_letter:
        m2 = _NAME_ONLY_RE.match(raw)
        if m2:
            name = m2.group("name").strip()
            if name:
                return name, int(default_class_number), str(default_class_letter)

    raise ValueError(f"Cannot parse student PDF filename: {filename!r}.")


# fixes20: translation removed.
_NON_RUSSIAN_RE = re.compile(r"[A-Za-zƏəÜüÖöÇçŞşĞğıİ]")


def needs_translation(name: str) -> bool:
    return bool(_NON_RUSSIAN_RE.search(name or ""))


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

    # stage18: помечаем, был ли использован fallback (UNKNOWN_CLASS_LETTER).
    class_was_unknown: bool = False

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
                        student_name, class_num, class_letter = parse_student_filename(
                            pdf["name"],
                            default_class_number=grade,
                            default_class_letter=UNKNOWN_CLASS_LETTER,
                        )
                    except ValueError as exc:
                        yield WalkResult(error=str(exc), error_at="pdf", **base)
                        continue
                    yield WalkResult(
                        student_name=student_name,
                        class_number=class_num,
                        class_letter=class_letter,
                        class_was_unknown=(class_letter == UNKNOWN_CLASS_LETTER),
                        **base,
                    )


# --- import ----------------------------------------------------------------


@dataclass
class ImportSummary:
    cohorts_created: int = 0
    students_created: int = 0
    students_skipped_dupes: int = 0
    students_flagged_for_review: int = 0
    students_unknown_class: int = 0
    names_translated: int = 0
    errors: list[str] = field(default_factory=list)


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
    owner_user_id: Optional[int] = None,
) -> ImportSummary:
    if test_date is None:
        test_date = date.today().isoformat()

    def _progress(stage: str, current: int, total: int) -> None:
        if progress_callback is not None:
            try: progress_callback(stage, current, total)
            except Exception: pass

    summary = ImportSummary()

    _progress("walking", 0, 0)
    results: list[WalkResult] = []
    for r in walk_root(service, root_folder_id, filters=filters):
        results.append(r)
        _progress("walking", len(results), len(results))

    cohort_cache: dict[tuple, int] = {}
    existing: dict[tuple, int] = {}
    existing_owners: dict[int, Optional[int]] = {}
    for c in db.list_cohorts():
        key = (c["school"], c["teacher"], c["class_number"], c["class_letter"],
               c["language"], c["grade"])
        existing[key] = c["id"]
        existing_owners[c["id"]] = c.get("owner_user_id")

    reactivated_ids: set[int] = set()

    def _ensure_active(cid: int) -> None:
        if cid in reactivated_ids:
            return
        reactivated_ids.add(cid)
        try:
            db.reactivate_cohort(cid)
        except AttributeError:
            pass
        if owner_user_id is not None and not existing_owners.get(cid):
            try:
                db.set_cohort_owner(cid, owner_user_id)
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
                owner_user_id=owner_user_id,
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
            # stage18: после fallback такое срабатывает редко, но возможно
            # (например, если .pdf вообще не в конце имени).
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
        if result.class_was_unknown:
            summary.students_unknown_class += 1

    _progress("saving", len(results), len(results))
    return summary
