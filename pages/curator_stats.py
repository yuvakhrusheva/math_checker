"""Curator Stats — admin-only page. «Кто сколько проверил.»

Разбивка по типу проверки:
  - manual_reviewed — работы, где куратор правил баллы вручную;
  - ai_accepted     — работы, где куратор принял ИИ-оценку без правок;
  - total_reviewed  — всего (manual + ai_accepted).

Считается по students.reviewed_by + task_results.manually_corrected.
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import src.auth as auth
import src.db as db

auth.require_role("admin")

st.title("📊 Curator Stats")
st.caption(
    "Сколько работ проверил каждый куратор, с разбивкой: правил вручную vs "
    "принял оценку ИИ без изменений. Считается любая работа, которую куратор "
    "отметил как Done или которой правил баллы."
)

# --- Date filter -----------------------------------------------------------
col1, col2, _ = st.columns([2, 2, 3])
with col1:
    d_from = st.date_input("From", value=date.today() - timedelta(days=30))
with col2:
    d_to = st.date_input("To", value=date.today())

date_from = d_from.isoformat() if d_from else None
date_to = d_to.isoformat() if d_to else None

# --- Summary ---------------------------------------------------------------
stats = db.get_review_stats(date_from=date_from, date_to=date_to)

if not stats:
    st.info(
        "Пока нет данных за выбранный период. Статистика появляется после того, "
        "как кураторы отмечают работы как Done или правят баллы."
    )
else:
    total_all = sum(s["total_reviewed"] for s in stats)
    manual_all = sum(s["manual_reviewed"] for s in stats)
    ai_all = sum(s["ai_accepted"] for s in stats)

    m1, m2, m3 = st.columns(3)
    m1.metric("Всего проверено", total_all)
    m2.metric("С ручными правками", manual_all)
    m3.metric("Принято от ИИ без правок", ai_all)

    df = pd.DataFrame(stats)
    df = df.rename(columns={
        "reviewed_by": "Куратор",
        "total_reviewed": "Всего",
        "manual_reviewed": "Вручную",
        "ai_accepted": "Принято от ИИ",
        "last_review": "Последняя проверка",
    })
    # Порядок колонок
    df = df[["Куратор", "Всего", "Вручную", "Принято от ИИ", "Последняя проверка"]]
    st.dataframe(df, use_container_width=True, hide_index=True)

    # --- Chart by day ------------------------------------------------------
    st.subheader("Проверки по дням")
    rows = db.get_reviewed_students(date_from=date_from, date_to=date_to)
    if rows:
        chart_df = pd.DataFrame(rows)
        chart_df["day"] = pd.to_datetime(chart_df["reviewed_at"]).dt.date
        pivot = (
            chart_df.groupby(["day", "reviewed_by"])
            .size()
            .reset_index(name="count")
            .pivot(index="day", columns="reviewed_by", values="count")
            .fillna(0)
        )
        st.bar_chart(pivot)
    else:
        st.caption("Нет проверок в выбранном диапазоне для графика.")

st.divider()
st.caption(
    "«Вручную» — куратор менял хотя бы один балл в работе. "
    "«Принято от ИИ» — куратор нажал Mark as Done, согласившись с оценкой ИИ. "
    "Авто-обработанные работы без участия куратора в счёт не попадают."
)
