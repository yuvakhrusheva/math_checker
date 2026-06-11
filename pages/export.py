"""Export screen — v3 (stage14).

stage14:
- На странице ТЕПЕРЬ виден предпросмотр результатов (таблица студентов с
  баллами по всем заданиям) — больше не нужно ждать .xlsx, чтобы убедиться,
  что данные сохранились.
- Куратор видит только свои когорты (для admin — все).
- Кнопка экспорта в Excel работает по тому же фильтру.
"""
import io
from datetime import datetime
from pathlib import Path

import streamlit as st

import src.db as db
import src.auth as auth

# Защита: страница доступна только залогиненным пользователям.
auth.require_login()
_CU = auth.current_user() or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TASK_NUMS = list(range(1, 17))


def _performance_level(total_score: float) -> str:
    if total_score >= 81:
        return "Advanced"
    elif total_score >= 61:
        return "Basic"
    elif total_score >= 31:
        return "Minimal"
    else:
        return "Insufficient"


def _build_export_rows(visible_cohort_ids: set[int]) -> tuple[list[dict], list[dict]]:
    """Собрать строки для двух листов (Answers / Scores).

    visible_cohort_ids — id когорт, видимых текущему пользователю. None — admin.
    """
    answer_rows = []
    score_rows = []

    cohorts = db.list_cohorts()
    cohort_map = {c["id"]: c for c in cohorts}

    target_cohorts = (
        list(cohorts) if visible_cohort_ids is None
        else [c for c in cohorts if c["id"] in visible_cohort_ids]
    )

    for cohort in target_cohorts:
        students = db.list_students_by_cohort(cohort["id"])
        for student in students:
            if student["status"] == "unreadable":
                continue

            is_error = student["status"] == "error"
            is_pending = (
                student["review_status"] == "pending"
                or student["status"] == "requires_review"
            )
            marker = "ERROR" if is_error else ("PENDING" if is_pending else None)

            results = {r["task_number"]: r for r in db.get_task_results(student["id"])}

            class_str = f"{cohort['class_number']}{cohort['class_letter']}"
            base = {
                "school": cohort["school"],
                "class": class_str,
                "teacher": cohort["teacher"],
                "test_date": cohort["test_date"],
                "student_id": student["id"],
            }
            variant = student["detected_variant"] if student["detected_variant"] is not None else ""
            display_name = student["recognized_name"] or student["filename"]

            total_score = 0.0
            task_answers = {}
            task_scores = {}
            for t in _TASK_NUMS:
                if marker:
                    task_answers[t] = marker
                    task_scores[t] = marker
                else:
                    r = results.get(t)
                    task_answers[t] = r["recognized_answer"] if r else ""
                    task_score = float(r["score"]) if r else 0.0
                    task_scores[t] = task_score
                    total_score += task_score

            ar = dict(base, recognized_name=display_name, variant=variant)
            for t in _TASK_NUMS:
                ar[f"task_{t}_answer"] = task_answers[t]
            answer_rows.append(ar)

            sr = dict(base, recognized_name=display_name, variant=variant)
            sr["total_score"] = None if marker else round(total_score, 2)
            for t in _TASK_NUMS:
                sr[f"task_{t}_score"] = task_scores[t]
            if marker:
                sr["performance_level"] = ""
            elif cohort["grade"] == 3:
                sr["performance_level"] = _performance_level(total_score)
            else:
                sr["performance_level"] = ""
            score_rows.append(sr)

    return answer_rows, score_rows


def _build_xlsx_bytes(answer_rows, score_rows) -> bytes:
    import pandas as pd
    df_a = pd.DataFrame(answer_rows) if answer_rows else pd.DataFrame()
    df_s = pd.DataFrame(score_rows) if score_rows else pd.DataFrame()

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_a.to_excel(writer, sheet_name="Answers", index=False)
        df_s.to_excel(writer, sheet_name="Scores", index=False)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("Export")

# Какие когорты видны текущему пользователю.
if _CU.get("role") == "admin":
    st.caption("👑 Ты admin — видишь все когорты.")
    visible_cohorts = db.list_cohorts()
    visible_cohort_ids = None  # None = no filter
else:
    visible_cohorts = db.list_cohorts_for_user(_CU.get("id"), _CU.get("role"))
    visible_cohort_ids = {c["id"] for c in visible_cohorts}
    st.caption("Видны только твои когорты.")

if not visible_cohorts:
    st.info("У тебя пока нет когорт.")
    st.stop()


# --- Предпросмотр результатов ---
st.subheader("📊 Результаты по работам")
st.caption(
    "Здесь видно, какие баллы сохранены в БД для каждой работы. PENDING — "
    "работа ждёт ручной проверки в Review Panel. ERROR — упала, без баллов."
)

answer_rows, score_rows = _build_export_rows(visible_cohort_ids)

if not score_rows:
    st.warning(
        "По твоим когортам пока нет данных. Возможные причины:\n"
        "- работы ещё не обработаны — запусти Start Processing в Cohort Queue;\n"
        "- ИИ не справился ни с одной — открой Review Panel и проверь вручную."
    )
else:
    import pandas as pd
    df_preview = pd.DataFrame(score_rows)
    # Удалим student_id из превью (он внутренний). Оставим в .xlsx.
    if "student_id" in df_preview.columns:
        df_preview_display = df_preview.drop(columns=["student_id"])
    else:
        df_preview_display = df_preview
    st.dataframe(df_preview_display, use_container_width=True, hide_index=True)
    st.caption(f"Всего записей в выгрузке: {len(score_rows)}")

st.divider()

# --- Кнопка экспорта в Excel ---
st.subheader("📥 Скачать Excel")
st.write(
    "Файл .xlsx с двумя листами: **Answers** (ответы учеников по заданиям) и "
    "**Scores** (баллы и уровни)."
)

if st.button("📥 Сформировать Excel", disabled=not score_rows):
    try:
        xlsx_bytes = _build_xlsx_bytes(answer_rows, score_rows)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        filename = f"results_{ts}.xlsx"
        st.download_button(
            label=f"⬇️ Download {filename}",
            data=xlsx_bytes,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.success(f"Файл готов: {filename}")
    except Exception as exc:
        st.error(f"Export failed: {exc}")

st.divider()

# --- Список нечитаемых работ ---
st.subheader("Unreadable Students")
st.caption("Работы, которые ИИ не смог прочитать (PDF битый / пустой).")

any_unreadable = False
for cohort in visible_cohorts:
    students = db.list_students_by_cohort(cohort["id"])
    unreadable = [s for s in students if s["status"] == "unreadable"]
    if not unreadable:
        continue
    any_unreadable = True
    st.write(
        f"**{cohort['school']} — {cohort['class_number']}{cohort['class_letter']}**"
    )
    for student in unreadable:
        name = student["recognized_name"] or student["filename"]
        error = student["error_message"] or "Unknown error"
        st.write(f"- `{name}`: {error}")

if not any_unreadable:
    st.info("No unreadable students.")
