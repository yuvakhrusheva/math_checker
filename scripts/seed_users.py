"""One-shot script to seed the first admin user(s).

Usage:
    python scripts/seed_users.py

Prompts interactively for password if SEED_ADMIN_PASSWORD env var is not set.
Safe to run multiple times — existing users are skipped.
"""
import getpass
import os
import sys
from pathlib import Path

# Make src/ importable when running from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.db as db        # noqa: E402
import src.auth as auth    # noqa: E402


# === ENTRIES — fill or edit before running ===
INITIAL_USERS = [
    {
        "username": "Liza",
        "display_name": "Елизавета",
        "role": "admin",
    },
]


def main() -> None:
    db.init_db()
    print(f"DB initialized. Seeding {len(INITIAL_USERS)} user(s)...")

    for spec in INITIAL_USERS:
        username = spec["username"]
        existing = auth.get_user_by_username(username)
        if existing:
            print(f"  - {username!r}: already exists, skipping.")
            continue

        env_pw_key = f"SEED_PASSWORD_{username.upper()}"
        password = os.environ.get(env_pw_key)
        if not password:
            password = getpass.getpass(
                f"Enter password for {username!r} ({spec['display_name']}): "
            )
            if not password:
                print(f"  - {username!r}: empty password, skipping.")
                continue

        uid = auth.create_user(
            username=username,
            display_name=spec["display_name"],
            password=password,
            role=spec["role"],
        )
        print(f"  - {username!r}: created (id={uid}, role={spec['role']}).")

    print("\nDone. You can now log in at http://localhost:8501.")


if __name__ == "__main__":
    main()
