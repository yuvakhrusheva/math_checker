"""Manage Users — admin-only page.

Что делает:
- Показывает таблицу всех пользователей (логин, ФИО, роль, email, дата создания).
- Кнопка «➕ Add user» — создать нового куратора или админа.
- Кнопка 🔑 на строке — сбросить пароль конкретного пользователя.
- Селект роли на строке — поменять curator / admin (себе менять нельзя).
- Кнопка 🗑 — удалить пользователя (себя удалить нельзя).

Доступ: только для role='admin'.
"""
import streamlit as st
from datetime import datetime

import src.auth as auth
import src.db as db
from sqlalchemy import update, delete

# Гейт по роли. Если не админ — st.stop с сообщением.
current = auth.require_role("admin")

st.title("👥 Manage Users")
st.caption(
    "Здесь админы заводят, удаляют и сбрасывают пароли коллегам. "
    "Куратор после создания заходит со своим логином и паролем на форме входа."
)


# ---------------------------------------------------------------------------
# Add user
# ---------------------------------------------------------------------------

with st.expander("➕ Add user", expanded=False):
    with st.form("add_user_form", clear_on_submit=True):
        username = st.text_input(
            "Username (логин)",
            help="Без пробелов, латиница. Например: ivanova, anna_k.",
        )
        display_name = st.text_input(
            "Display name (ФИО)",
            help="Как будет отображаться в UI: «Анна Иванова».",
        )
        role = st.selectbox("Role", ["curator", "admin"], index=0)
        password = st.text_input(
            "Initial password", type="password",
            help="Пользователь сможет сменить позже сам (когда сделаем эту фичу) "
                 "или ты сбросишь через 🔑.",
        )

        submitted = st.form_submit_button("Create user")

    if submitted:
        if not username or not display_name or not password:
            st.error("Username, display name и password обязательны.")
        else:
            try:
                uid = auth.create_user(
                    username=username.strip(),
                    display_name=display_name.strip(),
                    password=password,
                    role=role,
                )
                st.success(f"Создан пользователь {username!r} (id={uid}, role={role}).")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


# ---------------------------------------------------------------------------
# Users table
# ---------------------------------------------------------------------------

st.subheader("All users")

users = auth.list_users()

if not users:
    st.info("Пока никого нет. Заведи первого через «Add user» сверху.")
else:
    # Шапка
    hdr = st.columns([2, 3, 1, 2, 2])
    hdr[0].write("**Username**")
    hdr[1].write("**Display name**")
    hdr[2].write("**Role**")
    hdr[3].write("**Created**")
    hdr[4].write("**Actions**")

    st.divider()

    for u in users:
        cols = st.columns([2, 3, 1, 2, 2])
        cols[0].write(f"`{u['username']}`")
        cols[1].write(u["display_name"] or "—")

        is_self = u["username"] == current["username"]

        # Role selector
        with cols[2]:
            if is_self:
                # Себе менять роль запрещено (защита от выстрела в ногу).
                st.write(f"**{u['role']}** 🔒")
            else:
                new_role = st.selectbox(
                    "role", ["curator", "admin"],
                    index=0 if u["role"] == "curator" else 1,
                    key=f"role_select_{u['id']}",
                    label_visibility="collapsed",
                )
                if new_role != u["role"]:
                    with db.get_engine().begin() as conn:
                        conn.execute(
                            update(db.users_table)
                            .where(db.users_table.c.id == u["id"])
                            .values(role=new_role)
                        )
                    st.success(f"{u['username']}: роль → {new_role}")
                    st.rerun()

        created = u.get("created_at")
        if isinstance(created, datetime):
            created_str = created.strftime("%Y-%m-%d %H:%M")
        else:
            created_str = str(created) if created else "—"
        cols[3].write(created_str)

        with cols[4]:
            # Сброс пароля
            if st.button("🔑", key=f"reset_{u['id']}", help="Reset password"):
                st.session_state[f"resetting_{u['id']}"] = True
            # Удалить (нельзя себя)
            if not is_self and st.button("🗑", key=f"delete_{u['id']}",
                                          help="Delete user"):
                st.session_state[f"confirming_delete_{u['id']}"] = True

        # Inline forms (раскрываются под строкой)
        if st.session_state.get(f"resetting_{u['id']}"):
            with st.container(border=True):
                with st.form(f"reset_form_{u['id']}", clear_on_submit=True):
                    new_pw = st.text_input(
                        f"New password for {u['username']!r}",
                        type="password", key=f"newpw_{u['id']}",
                    )
                    c1, c2 = st.columns(2)
                    if c1.form_submit_button("Set password"):
                        if not new_pw:
                            st.error("Пароль не может быть пустым.")
                        else:
                            try:
                                auth.set_user_password(u["username"], new_pw)
                                st.success(f"Пароль обновлён для {u['username']!r}.")
                                del st.session_state[f"resetting_{u['id']}"]
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                    if c2.form_submit_button("Cancel"):
                        del st.session_state[f"resetting_{u['id']}"]
                        st.rerun()

        if st.session_state.get(f"confirming_delete_{u['id']}"):
            with st.container(border=True):
                st.warning(
                    f"Удалить пользователя `{u['username']}`? "
                    "Это действие нельзя отменить."
                )
                c1, c2 = st.columns(2)
                if c1.button("Yes, delete", key=f"confirm_del_{u['id']}",
                             type="primary"):
                    with db.get_engine().begin() as conn:
                        conn.execute(
                            delete(db.users_table)
                            .where(db.users_table.c.id == u["id"])
                        )
                    del st.session_state[f"confirming_delete_{u['id']}"]
                    st.success(f"Пользователь {u['username']!r} удалён.")
                    st.rerun()
                if c2.button("Cancel", key=f"cancel_del_{u['id']}"):
                    del st.session_state[f"confirming_delete_{u['id']}"]
                    st.rerun()

        st.divider()
