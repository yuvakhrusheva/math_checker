"""Pytest-level bootstrap: создать временную БД ДО импорта pages.* модулей.

Зачем этот файл существует:
pages/main.py и pages/review_panel.py выполняют db.list_cohorts() и
db.list_requires_review() на верхнем уровне модуля (это «тело» Streamlit-страниц).
Когда pytest импортирует эти модули для юнит-тестов, эти вызовы попадают в
неинициализированную БД и падают с `no such table: cohorts/students`.

Решение: до того как pytest начнёт собирать тесты, мы:
  1. Создаём отдельный временный файл-БД в системной TMP-папке.
  2. Подменяем src.db.DB_PATH на этот путь (на уровне модуля).
  3. Вызываем db.init_db() — создаются все нужные таблицы.

После этого любой импорт pages.main / pages.review_panel выполняется
корректно: db.list_*() возвращает пустой список, а не падает.

Тесты, которые делают свой monkeypatch.setattr(db_module, "DB_PATH", ...)
продолжают работать как раньше: monkeypatch временно меняет путь на
индивидуальный tmp_path/test.db, а после теста возвращает к нашему bootstrap
пути (а не к боевому "data/math_checker.db" из репозитория).
"""
import tempfile
from pathlib import Path

import src.db as db

# Создаём один общий tmp-каталог на всю pytest-сессию
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="math_checker_pytest_")
_BOOTSTRAP_DB = str(Path(_BOOTSTRAP_DIR) / "bootstrap.db")

# Подменяем глобальный DB_PATH ДО того как pytest начнёт импортировать
# тестовые модули (которые в свою очередь импортируют pages.*).
db.DB_PATH = _BOOTSTRAP_DB
db.init_db()
