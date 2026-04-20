"""
math_checker — Streamlit entry point.

Validates environment on startup, initialises the database, and routes to
the four application pages via st.navigation.
"""
import os
import sys

from dotenv import load_dotenv

# Load .env file before anything else
load_dotenv()


def validate_config() -> None:
    """
    Validate required environment variables.
    Raises ValueError with a clear message on any problem.
    Must be called before any Streamlit UI is rendered.
    """
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not key_path:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
            "Set it to the absolute path of your Google service account JSON key file "
            "(e.g. /home/user/keys/math_checker_sa.json). "
            "The file must be stored OUTSIDE the repository root."
        )

    if not os.path.isabs(key_path):
        raise ValueError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON must be an absolute path, "
            f"but got a relative path: '{key_path}'. "
            "Use a full path such as /home/user/keys/service_account.json."
        )

    if not os.path.isfile(key_path):
        raise ValueError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON points to a file that does not exist: '{key_path}'. "
            "Make sure the file exists and the path is correct."
        )


def main() -> None:
    import streamlit as st
    from src.db import init_db

    # Fail fast — validate config before any UI
    try:
        validate_config()
    except ValueError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()

    # Initialise database (creates tables if they don't exist)
    init_db()

    # Page routing
    pages = [
        st.Page("pages/criteria_management.py", title="Criteria Management", icon="📋"),
        st.Page("pages/main.py", title="Cohort Queue", icon="📂"),
        st.Page("pages/review_panel.py", title="Review Panel", icon="🔍"),
        st.Page("pages/export.py", title="Export", icon="📊"),
    ]
    pg = st.navigation(pages)
    pg.run()


if __name__ == "__main__" or "streamlit" in sys.modules:
    main()
