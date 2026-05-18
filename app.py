"""
math_checker — Streamlit entry point.

Validates environment on startup, initialises the database, and routes to
the four application pages via st.navigation.
"""
import os

from dotenv import load_dotenv

# Load .env file before anything else
load_dotenv()


def validate_config() -> None:
    """
    Validate required environment variables.
    Raises ValueError with a clear message on any problem.
    Must be called before any Streamlit UI is rendered.

    Security notes:
    - Checks that the service account key path is absolute (no traversal via relative paths).
    - Resolves symlinks and verifies the real path stays outside repo root.
    """
    key_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not key_path:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
            "Set it to the absolute path of your Google service account JSON key file "
            "stored OUTSIDE the repository root."
        )

    if not os.path.isabs(key_path):
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON must be an absolute path, "
            "but a relative path was provided. "
            "Use a full absolute path such as /home/user/keys/service_account.json."
        )

    real_path = os.path.realpath(key_path)

    if not os.path.isfile(real_path):
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON points to a file that does not exist. "
            "Make sure the file exists and the path in .env is correct."
        )

    # Ensure the key file is not inside the repository root
    repo_root = os.path.realpath(os.path.dirname(__file__))
    if real_path.startswith(repo_root + os.sep) or real_path == repo_root:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON must point to a file OUTSIDE the repository root. "
            "Move the key file to a directory outside the project folder."
        )


def main() -> None:
    import streamlit as st
    from src.db import init_db

    # Fail fast — validate config before any UI
    try:
        validate_config()
    except ValueError as exc:
        st.error(
            f"⚠️ Configuration error: {exc}\n\n"
            "Please check your `.env` file and restart the app."
        )
        st.stop()

    # Initialise database (creates tables if they don't exist)
    init_db()

    # Page routing — all four pages registered here (sole owner of st.navigation)
    pages = [
        st.Page("pages/criteria_management.py", title="Criteria Management", icon="📋"),
        st.Page("pages/main.py", title="Cohort Queue", icon="📂"),
        st.Page("pages/review_panel.py", title="Review Panel", icon="🔍"),
        st.Page("pages/export.py", title="Export", icon="📊"),
    ]
    pg = st.navigation(pages)
    pg.run()


# Streamlit executes this file as __main__ when running `streamlit run app.py`
if __name__ == "__main__":
    main()
