"""Полный сброс данных по когортам и ученикам.

Запускать НА СЕРВЕРЕ из папки ~/math_checker:
    source .venv/bin/activate
    python scripts/reset_cohorts.py

Что делает:
    - Очищает task_results (баллы по заданиям).
    - Очищает students (ученики).
    - Очищает cohorts (когорты).
    - НЕ трогает users (логины не теряются).
    - Опционально удаляет кэш скачанных PDF из data/downloads/.

После сброса можно заново нажать Browse + Import selected в Cohort Queue —
вся БД будет чистая.
"""
import os
import shutil
import sys
from pathlib import Path

# Чтобы импорты src.* работали при запуске из корня репо.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

import src.db as db


def _count(conn, table: str) -> int:
    row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
    return int(row[0]) if row else 0


def main():
    engine = db.get_engine()

    # --- Превью ---
    print("=" * 50)
    print("PREVIEW (что сейчас в БД):")
    print("=" * 50)
    with engine.connect() as conn:
        n_cohorts = _count(conn, "cohorts")
        n_students = _count(conn, "students")
        n_task_results = _count(conn, "task_results")
        n_users = _count(conn, "users")
    print(f"  cohorts:       {n_cohorts}")
    print(f"  students:      {n_students}")
    print(f"  task_results:  {n_task_results}")
    print(f"  users:         {n_users}  (НЕ удаляем)")
    print()

    if n_cohorts == 0 and n_students == 0 and n_task_results == 0:
        print("Уже пусто, нечего сбрасывать. Выход.")
        return

    # --- Подтверждение ---
    confirm = input(
        "Точно удалить ВСЕ когорты + учеников + баллы? "
        "Пользователи останутся. (введи 'YES' заглавными): "
    ).strip()
    if confirm != "YES":
        print("Отменено.")
        return

    # --- Удаление в правильном порядке (учитывая FK) ---
    with engine.begin() as conn:
        # task_results → ссылается на students, удаляем первой
        conn.execute(text("DELETE FROM task_results"))
        # students → ссылается на cohorts
        conn.execute(text("DELETE FROM students"))
        # cohorts → самостоятельная
        conn.execute(text("DELETE FROM cohorts"))

        # Сбросить автоинкремент (чтобы новые когорты начались с id=1).
        dialect = engine.dialect.name
        if dialect == "postgresql":
            conn.execute(text("ALTER SEQUENCE cohorts_id_seq RESTART WITH 1"))
            conn.execute(text("ALTER SEQUENCE students_id_seq RESTART WITH 1"))
            conn.execute(text("ALTER SEQUENCE task_results_id_seq RESTART WITH 1"))
        elif dialect == "sqlite":
            # В sqlite автоинкремент хранится в sqlite_sequence
            conn.execute(text(
                "DELETE FROM sqlite_sequence WHERE name IN "
                "('cohorts', 'students', 'task_results')"
            ))

    print("✅ БД очищена (cohorts / students / task_results).")
    print()

    # --- Удалить кэш PDF ---
    downloads_dir = Path("data/downloads")
    if downloads_dir.exists():
        size_mb = 0.0
        for root, dirs, files in os.walk(downloads_dir):
            for f in files:
                size_mb += os.path.getsize(os.path.join(root, f))
        size_mb /= 1024 * 1024
        ans = input(
            f"Удалить кэш скачанных PDF в {downloads_dir}/ "
            f"(~{size_mb:.1f} МБ)? [y/N]: "
        ).strip().lower()
        if ans == "y":
            shutil.rmtree(downloads_dir)
            downloads_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ Очищен {downloads_dir}/.")
        else:
            print(f"Кэш {downloads_dir}/ оставлен.")
    else:
        print(f"Папка {downloads_dir}/ не существует — пропускаю.")

    print()
    print("Готово. Теперь можно заново открыть Cohort Queue и сделать импорт.")


if __name__ == "__main__":
    main()
