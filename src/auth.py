"""Authentication & session helpers for math_checker (stage1).

Простой логин/пароль на bcrypt + хранение сессии в Streamlit session_state.
Пользователи лежат в таблице users (см. src/db.py). Поля:
  - username       (логин, уникальный)
  - display_name   (ФИО для UI)
  - password_hash  (bcrypt)
  - role           ('curator' | 'admin')
  - email          (опционально, для сброса пароля)

Public API:
    login_form()           — рисует форму логина в st (если ещё не вошёл)
                             и возвращает dict пользователя или None.
    require_login()        — то же, но если пользователя нет — st.stop().
                             Возвращает dict пользователя.
    require_role('admin')  — проверка роли. Если не подходит — st.error+st.stop.
    logout_button()        — кнопка выхода в сайдбаре.
    current_user()         — текущий пользователь из session_state или None.
    create_user(...)       — создать запись пользователя (используется
                             админ-скриптом и страницей Manage Users).
    verify_password(...)   — для login_form.
    hash_password(plain)   — для seed-скриптов и страницы создания.
"""
from __future__ import annotations

import os
from typing import Optional

import bcrypt
import streamlit as st
from sqlalchemy import select, update

import src.db as db


SESSION_KEY = "auth_user"

# --- Persistent login via signed cookie -----------------------------------
# Сессия Streamlit живёт только в памяти и сбрасывается при F5. Чтобы вход
# «запоминался», кладём подписанный токен в cookie браузера и восстанавливаем
# из него сессию при загрузке страницы.

_COOKIE_NAME = "mc_auth"
_COOKIE_DAYS = 7  # сколько дней «помнить» вход

try:
    from streamlit_cookies_controller import CookieController  # type: ignore
    _COOKIES_AVAILABLE = True
except Exception:
    _COOKIES_AVAILABLE = False


def _auth_secret() -> str:
    return os.environ.get("AUTH_SECRET", "dev-insecure-secret-change-me")


def _get_cookie_controller():
    """Один CookieController на сессию (или None, если библиотека не стоит)."""
    if not _COOKIES_AVAILABLE:
        return None
    if "_cookie_ctrl" not in st.session_state:
        try:
            st.session_state["_cookie_ctrl"] = CookieController()
        except Exception:
            return None
    return st.session_state["_cookie_ctrl"]


def _make_token(username: str, days: int = _COOKIE_DAYS) -> str:
    import base64, hashlib, hmac, time
    exp = int(time.time()) + days * 86400
    payload = f"{username}|{exp}"
    sig = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}|{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _verify_token(token: str) -> Optional[str]:
    import base64, hashlib, hmac, time
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, exp, sig = raw.rsplit("|", 2)
        payload = f"{username}|{exp}"
        expected = hmac.new(_auth_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp) < time.time():
            return None
        return username
    except Exception:
        return None


def _set_login_cookie(username: str) -> None:
    """Поставить cookie через JS (синхронно в браузере) и перезагрузить страницу.

    Streamlit st.rerun() не ждёт асинхронных компонентов, поэтому cookie через
    CookieController.set часто НЕ успевал записаться. Здесь JS пишет cookie
    через document.cookie и тут же перезагружает страницу — при перезагрузке
    cookie уже в браузере, и st.context.cookies его увидит.
    """
    import streamlit.components.v1 as components
    token = _make_token(username)
    max_age = _COOKIE_DAYS * 86400
    components.html(
        f"""
        <script>
        document.cookie = "{_COOKIE_NAME}={token}; max-age={max_age}; path=/; samesite=lax";
        // Дать браузеру записать cookie и перезагрузить страницу.
        setTimeout(function() {{ window.parent.location.reload(); }}, 50);
        </script>
        """,
        height=0,
    )


def _clear_login_cookie() -> None:
    """Удалить cookie через JS и перезагрузить страницу."""
    import streamlit.components.v1 as components
    components.html(
        f"""
        <script>
        document.cookie = "{_COOKIE_NAME}=; max-age=0; path=/; samesite=lax";
        setTimeout(function() {{ window.parent.location.reload(); }}, 50);
        </script>
        """,
        height=0,
    )


def _read_cookie_token() -> Optional[str]:
    """Прочитать токен из cookie. Сначала СИНХРОННО через st.context.cookies
    (Streamlit >= 1.42) — это переживает F5. Если недоступно — через
    CookieController (асинхронный, может не сработать на первом ране).
    """
    # 1) Синхронное чтение из HTTP-запроса (надёжно при перезагрузке).
    try:
        token = st.context.cookies.get(_COOKIE_NAME)
        if token:
            return token
    except Exception:
        pass
    # 2) Fallback: CookieController.
    ctrl = _get_cookie_controller()
    if ctrl is not None:
        try:
            return ctrl.get(_COOKIE_NAME)
        except Exception:
            return None
    return None


def _restore_session_from_cookie() -> Optional[dict]:
    """Если в cookie валидный токен — восстановить сессию. Иначе None."""
    token = _read_cookie_token()
    if not token:
        return None
    username = _verify_token(token)
    if not username:
        return None
    u = get_user_by_username(username)
    if u is None:
        return None
    st.session_state[SESSION_KEY] = {
        "id": u["id"],
        "username": u["username"],
        "display_name": u["display_name"],
        "role": u["role"],
        "email": u.get("email"),
    }
    return st.session_state[SESSION_KEY]


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    if not isinstance(plain, str) or not plain:
        raise ValueError("Password must be a non-empty string")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------

def get_user_by_username(username: str) -> Optional[dict]:
    if not username:
        return None
    with db.get_engine().connect() as conn:
        row = conn.execute(
            select(db.users_table).where(db.users_table.c.username == username)
        ).mappings().first()
    return dict(row) if row else None


def create_user(
    username: str,
    display_name: str,
    password: str,
    role: str = "curator",
    email: Optional[str] = None,
) -> int:
    """Create a new user. Returns its id. Raises ValueError if username taken."""
    if role not in ("curator", "admin"):
        raise ValueError(f"role must be 'curator' or 'admin', got {role!r}")
    if get_user_by_username(username) is not None:
        raise ValueError(f"User {username!r} already exists")
    with db.get_engine().begin() as conn:
        cur = conn.execute(
            db.users_table.insert().values(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                role=role,
                email=email,
            )
        )
        return cur.inserted_primary_key[0]


def set_user_password(username: str, new_password: str) -> None:
    if get_user_by_username(username) is None:
        raise ValueError(f"User {username!r} not found")
    with db.get_engine().begin() as conn:
        conn.execute(
            update(db.users_table)
            .where(db.users_table.c.username == username)
            .values(password_hash=hash_password(new_password))
        )


def update_user_profile(username: str, display_name: str | None = None) -> None:
    """Обновить display_name пользователя."""
    if display_name is None:
        return
    if not display_name.strip():
        raise ValueError("Имя не может быть пустым")
    if get_user_by_username(username) is None:
        raise ValueError(f"User {username!r} not found")
    with db.get_engine().begin() as conn:
        conn.execute(
            update(db.users_table)
            .where(db.users_table.c.username == username)
            .values(display_name=display_name.strip())
        )


def change_own_password(username: str, old_password: str, new_password: str) -> None:
    """Сменить свой пароль: проверяем старый, ставим новый.

    Raises ValueError если старый пароль неверный или нового нет.
    """
    u = get_user_by_username(username)
    if u is None:
        raise ValueError("User not found")
    if not verify_password(old_password, u["password_hash"]):
        raise ValueError("Текущий пароль неверный")
    if not new_password:
        raise ValueError("Новый пароль не может быть пустым")
    if len(new_password) < 6:
        raise ValueError("Новый пароль должен быть не короче 6 символов")
    set_user_password(username, new_password)


def list_users() -> list[dict]:
    with db.get_engine().connect() as conn:
        rows = conn.execute(
            select(db.users_table).order_by(db.users_table.c.username)
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Streamlit integration
# ---------------------------------------------------------------------------

def current_user() -> Optional[dict]:
    return st.session_state.get(SESSION_KEY)


def login_form() -> Optional[dict]:
    """Render the login form unless already logged in. Returns user dict or None."""
    user = current_user()
    if user is not None:
        return user

    # Попытка восстановить вход из cookie (переживает перезагрузку страницы).
    restored = _restore_session_from_cookie()
    if restored is not None:
        return restored

    st.title("🔐 Sign in to math_checker")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        if not username or not password:
            st.error("Введи логин и пароль.")
            return None
        u = get_user_by_username(username.strip())
        if u is None or not verify_password(password, u["password_hash"]):
            st.error("Неверный логин или пароль.")
            return None
        st.session_state[SESSION_KEY] = {
            "id": u["id"],
            "username": u["username"],
            "display_name": u["display_name"],
            "role": u["role"],
            "email": u.get("email"),
        }
        _set_login_cookie(u["username"])
        # JS внутри _set_login_cookie сам перезагрузит страницу.
        st.stop()
    return None


def require_login() -> dict:
    """Block the rest of the page unless logged in. Returns user dict."""
    user = login_form()
    if user is None:
        st.stop()
    return user  # type: ignore[return-value]


def require_role(role: str) -> dict:
    """Block the rest of the page unless logged in AND user has given role."""
    user = require_login()
    if user["role"] != role:
        st.error(
            f"Эта страница доступна только пользователям с ролью **{role}**. "
            f"Твоя роль: **{user['role']}**."
        )
        st.stop()
    return user


def logout_button() -> None:
    """Render a small logout block in the sidebar."""
    user = current_user()
    if user is None:
        return
    with st.sidebar:
        st.markdown(f"👤 **{user['display_name']}**")
        st.caption(f"@{user['username']} · role: {user['role']}")
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.pop(SESSION_KEY, None)
            _clear_login_cookie()
            # JS сам перезагрузит страницу.
            st.stop()
