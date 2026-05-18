"""Pytest-level bootstrap для math_checker.

Зачем этот файл:
1. pages/main.py и pages/review_panel.py выполняют db.list_* на верхнем уровне
   модуля (тело Streamlit-страниц). Без инициализированной БД импорт этих
   модулей в тестах падает с no such table.
2. Поэтому до того, как pytest начнёт собирать тесты, мы создаём временный
   SQLite-файл и инициализируем в нём схему.
3. Тесты, которые сами делают monkeypatch.setattr(db, "DB_PATH", tmp_path/...)
   продолжают работать — они получают свой engine на свой tmp-файл (через
   _resolve_database_url() и кэш get_engine).

После каждого теста сбрасываем кэш engine, чтобы не накапливать соединения
к временным БД.
"""
import tempfile
from pathlib import Path

import pytest

import src.db as db


# Один общий bootstrap-каталог на всю pytest-сессию
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="math_checker_pytest_")
_BOOTSTRAP_DB = str(Path(_BOOTSTRAP_DIR) / "bootstrap.db")

# Подменяем глобальный DB_PATH ДО любых импортов pages.*
db.DB_PATH = _BOOTSTRAP_DB
db.init_db()


@pytest.fixture(autouse=True)
def _reset_engine_after_test():
    """После каждого теста сбрасываем кэш SQLAlchemy engine.

    Это предотвращает накопление соединений к временным БД (tmp_path),
    которые были созданы тестами через monkeypatch DB_PATH.
    """
    yield
    db.reset_engine_cache()
    # Возвращаем глобальный DB_PATH к bootstrap-пути, чтобы код, который
    # выполняется ПОСЛЕ теста (например, при cleanup), не пытался открыть
    # рабочую БД в data/.
    db.DB_PATH = _BOOTSTRAP_DB
