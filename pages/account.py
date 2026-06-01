"""My Account — редактирование своих данных и смена пароля.

Доступна любому залогиненному пользователю. Здесь можно:
  - изменить отображаемое имя (display_name),
  - сменить пароль.
"""
import streamlit as st

import src.auth as auth

# Защита: только для залогиненных.
user = auth.require_login()

st.title("👤 My Account")
st.write(f"`@{user['username']}`  ·  role: {user['role']}")

st.divider()

# ---------------------------------------------------------------------------
# Edit profile
# ---------------------------------------------------------------------------
st.subheader("✏️ Edit display name")

with st.form("edit_profile_form"):
    display_name = st.text_input("Display name (ФИО)", value=user.get("display_name") or "")
    saved = st.form_submit_button("Save")

if saved:
    try:
        auth.update_user_profile(user["username"], display_name=display_name)
        st.session_state[auth.SESSION_KEY]["display_name"] = display_name.strip()
        st.success("Имя обновлено.")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))

st.divider()

# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------
st.subheader("🔑 Change my password")

with st.form("change_password_form", clear_on_submit=True):
    old_pw = st.text_input("Current password", type="password")
    new_pw = st.text_input("New password", type="password", help="Не короче 6 символов.")
    new_pw2 = st.text_input("Repeat new password", type="password")
    submitted = st.form_submit_button("Change password")

if submitted:
    if new_pw != new_pw2:
        st.error("Новые пароли не совпадают.")
    else:
        try:
            auth.change_own_password(user["username"], old_pw, new_pw)
            st.success("Пароль изменён. В следующий раз входи с новым паролем.")
        except ValueError as exc:
            st.error(str(exc))
