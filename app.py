"""math_checker — Streamlit entry point with login gate + navigation.

Объединяет старую конфигурацию (validate_config + st.navigation с иконками)
с новой формой авторизации (stage1). Страница «Manage Users» появляется в
сайдбаре только когда залогинен админ.
"""
import os

from dotenv import load_dotenv

# Load .env BEFORE any Streamlit / SQLAlchemy import that depends on it.
load_dotenv()


def validate_config() -> None:
    """Validate required environment variables. Raise ValueError on problems."""
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not key_path:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
            "Set it to the absolute path of your Google service account JSON key "
            "file stored OUTSIDE the repository root."
        )

    if not os.path.isabs(key_path):
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON must be an absolute path."
        )

    real_path = os.path.realpath(key_path)
    if not os.path.isfile(real_path):
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON points to a missing file.")

    repo_root = os.path.realpath(os.path.dirname(__file__))
    if real_path.startswith(repo_root + os.sep) or real_path == repo_root:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON must point to a file OUTSIDE the repo root."
        )


def main() -> None:
    import streamlit as st
    from src.db import init_db
    import src.auth as auth

    st.set_page_config(page_title="Math Checker", layout="wide")

    # Fail fast on bad config.
    try:
        validate_config()
    except ValueError as exc:
        st.error(f"⚠️ Configuration error: {exc}\n\nProverь .env и перезапусти.")
        st.stop()

    init_db()

    # --- Login gate -------------------------------------------------------
    user = auth.login_form()
    if user is None:
        st.stop()

    # Sidebar: user info + logout (always visible after login).
    auth.logout_button()

    # --- Navigation -------------------------------------------------------
    pages = [
        st.Page("pages/criteria_management.py",
                title="Criteria Management", icon="📋"),
        st.Page("pages/main.py", title="Cohort Queue", icon="📂"),
        st.Page("pages/review_panel.py", title="Review Panel", icon="🔍"),
        st.Page("pages/export.py", title="Export", icon="📊"),
        st.Page("pages/account.py", title="My Account", icon="👤"),
    ]

    # Admin-only pages
    if user["role"] == "admin":
        pages.append(
            st.Page("pages/manage_users.py", title="Manage Users", icon="👥")
        )
        pages.append(
            st.Page("pages/curator_stats.py", title="Curator Stats", icon="📈")
        )

    pg = st.navigation(pages)
    pg.run()


if __name__ == "__main__":
    main()
