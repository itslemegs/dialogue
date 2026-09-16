#!/usr/bin/env python3

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import select

from app.db import get_session, grant_role
from app.models import User
from app.security import hash_password


ACCOUNTS = [
    ("shibata_ta",   "shibata@ta.iplab.jp"),
    ("taniguchi_ta", "taniguchi@ta.iplab.jp"),
    ("ogoshi_ta",    "ogoshi@ta.iplab.jp"),
    ("mukai_ta",     "mukai@ta.iplab.jp"),
    ("tani_ta",      "tani@ta.iplab.jp"),
    ("shun_ta",      "shun@ta.iplab.jp"),
]


def main():
    parser = argparse.ArgumentParser(
        description="Create the six d¡alogüe TA accounts."
    )
    parser.add_argument(
        "--create",
        action="store_true",
        help="Actually create missing accounts. Default is preview only.",
    )
    args = parser.parse_args()

    planned = []
    conflicts = []

    with get_session() as db:
        for handle, email in ACCOUNTS:
            by_handle = db.exec(
                select(User).where(User.handle == handle)
            ).first()

            by_email = db.exec(
                select(User).where(User.email == email)
            ).first()

            if by_handle is None and by_email is None:
                planned.append(("CREATE", handle, email, None))
                continue

            if (
                by_handle is not None
                and by_email is not None
                and by_handle.id == by_email.id
                and by_handle.handle == handle
                and by_handle.email == email
            ):
                planned.append(("EXISTS", handle, email, by_handle.id))
                continue

            conflicts.append(
                {
                    "handle": handle,
                    "email": email,
                    "handle_match": (
                        None
                        if by_handle is None
                        else {
                            "id": by_handle.id,
                            "handle": by_handle.handle,
                            "email": by_handle.email,
                        }
                    ),
                    "email_match": (
                        None
                        if by_email is None
                        else {
                            "id": by_email.id,
                            "handle": by_email.handle,
                            "email": by_email.email,
                        }
                    ),
                }
            )

        print("\nTA account plan:")
        print("-" * 72)

        for status, handle, email, user_id in planned:
            suffix = f"  id={user_id}" if user_id is not None else ""
            print(
                f"{status:<8} "
                f"{handle:<18} "
                f"{email:<30}"
                f"{suffix}"
            )

        if conflicts:
            print("\nCONFLICTS:")
            for item in conflicts:
                print(
                    f"\nRequested: "
                    f"{item['handle']} / {item['email']}"
                )
                print(
                    f"  handle match: {item['handle_match']}"
                )
                print(
                    f"  email match:  {item['email_match']}"
                )

            print("\nABORT: resolve conflicts before creating accounts.")
            raise SystemExit(2)

        missing = [
            (handle, email)
            for status, handle, email, _ in planned
            if status == "CREATE"
        ]

        existing_count = sum(
            1
            for status, _, _, _ in planned
            if status == "EXISTS"
        )

        print(
            f"\nSummary: "
            f"{len(missing)} to create, "
            f"{existing_count} already exist."
        )

        if not args.create:
            print("\nPREVIEW ONLY — no changes made.")
            print(
                "Run again with --create to create the missing accounts."
            )
            return

        if not missing:
            print("\nAll six TA accounts already exist.")
            return

        password = getpass.getpass(
            "\nPassword for all TA accounts: "
        )
        confirm = getpass.getpass(
            "Confirm password: "
        )

        if not password:
            raise SystemExit("Password cannot be empty.")

        if password != confirm:
            raise SystemExit("Passwords do not match. No accounts created.")

        print()

        created = []

        for handle, email in missing:
            user = User(
                handle=handle,
                email=email,
                password_hash=hash_password(password),
                verified=True,
            )

            db.add(user)
            db.commit()
            db.refresh(user)

            # Same baseline role granted by the normal /register route.
            grant_role(db, user, "member")

            created.append(user)
            print(
                f"CREATED  id={user.id:<5} "
                f"{user.handle:<18} {user.email}"
            )

    print(
        f"\nDone. Created {len(created)} account(s)."
    )


if __name__ == "__main__":
    main()
