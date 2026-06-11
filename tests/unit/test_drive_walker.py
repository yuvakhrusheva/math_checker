"""Unit tests for src/drive_walker.py — parsers and walk logic."""
import pytest


# --- parse_sector_folder ---

class TestParseSectorFolder:
    def test_az_sector_cyrillic(self):
        from src.drive_walker import parse_sector_folder
        assert parse_sector_folder("Аз сектор") == "az"
        assert parse_sector_folder("аз сектор") == "az"
        assert parse_sector_folder("АЗ СЕКТОР") == "az"

    def test_ru_sector(self):
        from src.drive_walker import parse_sector_folder
        assert parse_sector_folder("Ру сектор") == "ru"
        assert parse_sector_folder("ру сектор") == "ru"

    def test_invalid_sector_raises(self):
        from src.drive_walker import parse_sector_folder
        with pytest.raises(ValueError):
            parse_sector_folder("English section")
        with pytest.raises(ValueError):
            parse_sector_folder("")


# --- parse_grade_folder ---

class TestParseGradeFolder:
    def test_two_class(self):
        from src.drive_walker import parse_grade_folder
        assert parse_grade_folder("2 класс") == 2
        assert parse_grade_folder("2 КЛАСС") == 2
        assert parse_grade_folder("  2 класс ") == 2

    def test_three_class(self):
        from src.drive_walker import parse_grade_folder
        assert parse_grade_folder("3 класс") == 3

    def test_just_number(self):
        from src.drive_walker import parse_grade_folder
        assert parse_grade_folder("2") == 2

    def test_invalid_grade_raises(self):
        from src.drive_walker import parse_grade_folder
        with pytest.raises(ValueError):
            parse_grade_folder("1 класс")
        with pytest.raises(ValueError):
            parse_grade_folder("foo")


# --- parse_teacher_folder ---

class TestParseTeacherFolder:
    def test_real_example_in_project(self):
        from src.drive_walker import parse_teacher_folder
        school, teacher, in_project = parse_teacher_folder(
            "Школа № 5 Хырдалан - Hüseynova Vüsalə Qabil (в проекте)"
        )
        assert school == "Школа № 5 Хырдалан"
        assert teacher == "Hüseynova Vüsalə Qabil"
        assert in_project is True

    def test_real_example_not_in_project(self):
        from src.drive_walker import parse_teacher_folder
        school, teacher, in_project = parse_teacher_folder(
            "Школа № 5 Хырдалан - Süleymanlı Günel Elşən (не в проекте)"
        )
        assert school == "Школа № 5 Хырдалан"
        assert teacher == "Süleymanlı Günel Elşən"
        assert in_project is False

    def test_no_status_in_parens(self):
        from src.drive_walker import parse_teacher_folder
        school, teacher, in_project = parse_teacher_folder(
            "Школа № 1 Баку - Иванова Анна Петровна"
        )
        assert school == "Школа № 1 Баку"
        assert teacher == "Иванова Анна Петровна"
        assert in_project is None

    def test_em_dash_works(self):
        from src.drive_walker import parse_teacher_folder
        # Длинное тире вместо обычного дефиса
        school, teacher, _ = parse_teacher_folder(
            "Школа № 7 — Петрова Мария"
        )
        assert school == "Школа № 7"
        assert teacher == "Петрова Мария"

    def test_invalid_no_dash_raises(self):
        from src.drive_walker import parse_teacher_folder
        with pytest.raises(ValueError):
            parse_teacher_folder("Иванова Анна без школы")


# --- parse_student_filename ---

class TestParseStudentFilename:
    def test_azerbaijani_two_word_name(self):
        from src.drive_walker import parse_student_filename
        name, num, letter = parse_student_filename("Abbaszadə Nəzrin 2d.pdf")
        assert name == "Abbaszadə Nəzrin"
        assert num == 2
        assert letter == "d"

    def test_russian_three_word_name(self):
        from src.drive_walker import parse_student_filename
        name, num, letter = parse_student_filename("Иванов Иван Иванович 2А.pdf")
        assert name == "Иванов Иван Иванович"
        assert num == 2
        assert letter == "А"

    def test_uppercase_extension(self):
        from src.drive_walker import parse_student_filename
        name, num, letter = parse_student_filename("Petrov Petr 3B.PDF")
        assert name == "Petrov Petr"
        assert num == 3
        assert letter == "B"

    def test_invalid_no_class_raises(self):
        from src.drive_walker import parse_student_filename
        with pytest.raises(ValueError):
            parse_student_filename("just_a_name.pdf")

    def test_invalid_no_pdf_raises(self):
        from src.drive_walker import parse_student_filename
        with pytest.raises(ValueError):
            parse_student_filename("Иванов Иван 2А")
