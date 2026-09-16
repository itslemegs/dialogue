#!/usr/bin/env python3

import argparse
import asyncio
import re
import sys
from pathlib import Path
from collections import defaultdict

# Allow this script to import the sibling app/ package when executed directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, inspect, select, update
from sqlmodel import SQLModel

from app import models  # noqa: F401 - loads SQLModel metadata
from app.db import engine


HANDLE_RE = re.compile(r"^loadtest\d{2}$", re.IGNORECASE)
EXPECTED_DOMAIN = "@example.invalid"

# Disposable user activity should be removed rather than orphaned.
DELETE_ALWAYS_TABLES = {
    "notification",
    "speakerrequest",
    "session",
    "event_access_grant",
    "amendment_vote",
    "proposal_early_ballot",
    "proposal_formal_ballot",
    "proposal_speaker_request",
    "userrolelink",
}


def inspect_database(sync_conn):
    insp = inspect(sync_conn)

    result = {
        "tables": {},
        "user_fks": set(),
    }

    for table_name in insp.get_table_names():
        columns = {
            col["name"]: {
                "nullable": bool(col.get("nullable")),
            }
            for col in insp.get_columns(table_name)
        }
        result["tables"][table_name] = columns

        for fk in insp.get_foreign_keys(table_name):
            if fk.get("referred_table") != "user":
                continue

            for column_name in fk.get("constrained_columns") or []:
                result["user_fks"].add((table_name, column_name))

    return result


def model_user_references(schema):
    refs = {}

    for table in SQLModel.metadata.tables.values():
        if table.name == "user":
            continue

        if table.name not in schema["tables"]:
            continue

        for column in table.columns:
            if column.name not in schema["tables"][table.name]:
                continue

            targets_user = any(
                fk.target_fullname == "user.id"
                for fk in column.foreign_keys
            )

            if not targets_user:
                continue

            actual_nullable = schema["tables"][table.name][column.name]["nullable"]

            refs[(table.name, column.name)] = {
                "table": table,
                "column": column,
                "nullable": actual_nullable,
            }

    return refs


async def find_users(conn):
    user_table = SQLModel.metadata.tables["user"]

    result = await conn.execute(
        select(
            user_table.c.id,
            user_table.c.handle,
            user_table.c.email,
        )
        .where(func.lower(user_table.c.handle).like("loadtest%"))
        .order_by(user_table.c.id)
    )

    matched = []

    for row in result:
        handle = (row.handle or "").strip()
        email = (row.email or "").strip()

        if not HANDLE_RE.fullmatch(handle):
            continue

        if email.lower() != f"{handle.lower()}{EXPECTED_DOMAIN}":
            continue

        matched.append(row)

    return matched


async def count_reference(conn, table, column, user_ids):
    result = await conn.execute(
        select(func.count())
        .select_from(table)
        .where(column.in_(user_ids))
    )
    return int(result.scalar_one())


async def main():
    parser = argparse.ArgumentParser(
        description="Safely remove disposable Locust load-test users."
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually perform the cleanup. Without this flag, preview only.",
    )
    parser.add_argument(
        "--purge-logs",
        action="store_true",
        help="Delete experiment_session_log rows instead of preserving them.",
    )
    args = parser.parse_args()

    async with engine.connect() as conn:
        schema = await conn.run_sync(inspect_database)
        users = await find_users(conn)

        if not users:
            print("No matching Locust users found.")
            return

        print(f"\nMatched Locust users: {len(users)}")
        for user in users:
            print(f"  id={user.id:<5} {user.handle:<16} {user.email}")

        user_ids = [user.id for user in users]
        refs = model_user_references(schema)

        # Safety check: if the live database has a FK to user that our
        # application metadata does not know about, stop rather than guess.
        model_ref_keys = set(refs)
        unknown_fks = schema["user_fks"] - model_ref_keys

        if unknown_fks:
            print("\nABORT: database contains unhandled references to user:")
            for table_name, column_name in sorted(unknown_fks):
                print(f"  {table_name}.{column_name}")
            print("\nNo changes made.")
            raise SystemExit(2)

        actions = []

        for key, ref in sorted(refs.items()):
            count = await count_reference(
                conn,
                ref["table"],
                ref["column"],
                user_ids,
            )

            if not count:
                continue

            action = (
                "DELETE ROW"
                if ref["table"].name in DELETE_ALWAYS_TABLES or not ref["nullable"]
                else "SET NULL"
            )
            actions.append(
                {
                    **ref,
                    "count": count,
                    "action": action,
                }
            )

        # experiment_session_log intentionally may not have a foreign key.
        log_action = None
        log_table = SQLModel.metadata.tables.get("experiment_session_log")

        if (
            log_table is not None
            and "experiment_session_log" in schema["tables"]
            and "user_id" in schema["tables"]["experiment_session_log"]
        ):
            log_column = log_table.c.user_id
            log_count = await count_reference(
                conn,
                log_table,
                log_column,
                user_ids,
            )

            if log_count:
                nullable = schema["tables"]["experiment_session_log"]["user_id"][
                    "nullable"
                ]

                if args.purge_logs or not nullable:
                    action = "DELETE ROW"
                else:
                    action = "SET NULL"

                log_action = {
                    "table": log_table,
                    "column": log_column,
                    "nullable": nullable,
                    "count": log_count,
                    "action": action,
                }

        print("\nRelated records:")
        if not actions and not log_action:
            print("  none")
        else:
            for item in actions:
                print(
                    f"  {item['table'].name}.{item['column'].name:<30} "
                    f"{item['count']:>6}   {item['action']}"
                )

            if log_action:
                print(
                    f"  experiment_session_log.user_id"
                    f"{'':<19} "
                    f"{log_action['count']:>6}   {log_action['action']}"
                )

        if not args.delete:
            print("\nPREVIEW ONLY — no changes made.")
            print(
                "Run with --delete after checking the counts:"
            )
            print(
                "  python scripts/delete_locust_users.py --delete"
            )
            return

    print("\nStarting cleanup transaction...")

    async with engine.begin() as conn:
        # Re-evaluate inside the write transaction.
        users = await find_users(conn)
        user_ids = [user.id for user in users]

        if not user_ids:
            print("No matching users remain.")
            return

        schema = await conn.run_sync(inspect_database)
        refs = model_user_references(schema)

        # First remove records with mandatory user ownership.
        # These are disposable Locust-authored/activity records.
        mandatory = [
            ref for ref in refs.values()
            if (
                not ref["nullable"]
                or ref["table"].name in DELETE_ALWAYS_TABLES
            )
        ]

        # Multiple user columns can exist in the same table. Repeated
        # deletes are harmless.
        for ref in mandatory:
            result = await conn.execute(
                delete(ref["table"])
                .where(ref["column"].in_(user_ids))
            )

            if result.rowcount:
                print(
                    f"DELETE {ref['table'].name}.{ref['column'].name}: "
                    f"{result.rowcount}"
                )

        # Preserve records whose user attribution is optional.
        for ref in refs.values():
            if (
                not ref["nullable"]
                or ref["table"].name in DELETE_ALWAYS_TABLES
            ):
                continue

            result = await conn.execute(
                update(ref["table"])
                .where(ref["column"].in_(user_ids))
                .values({ref["column"].name: None})
            )

            if result.rowcount:
                print(
                    f"NULL   {ref['table'].name}.{ref['column'].name}: "
                    f"{result.rowcount}"
                )

        # Preserve semantic experiment logs by anonymising their user
        # link, unless --purge-logs was explicitly requested.
        log_table = SQLModel.metadata.tables.get("experiment_session_log")

        if (
            log_table is not None
            and "experiment_session_log" in schema["tables"]
            and "user_id" in schema["tables"]["experiment_session_log"]
        ):
            log_column = log_table.c.user_id
            nullable = schema["tables"]["experiment_session_log"]["user_id"][
                "nullable"
            ]

            if args.purge_logs or not nullable:
                result = await conn.execute(
                    delete(log_table)
                    .where(log_column.in_(user_ids))
                )
                if result.rowcount:
                    print(
                        "DELETE experiment_session_log: "
                        f"{result.rowcount}"
                    )
            else:
                result = await conn.execute(
                    update(log_table)
                    .where(log_column.in_(user_ids))
                    .values(user_id=None)
                )
                if result.rowcount:
                    print(
                        "NULL   experiment_session_log.user_id: "
                        f"{result.rowcount}"
                    )

        user_table = SQLModel.metadata.tables["user"]

        result = await conn.execute(
            delete(user_table)
            .where(user_table.c.id.in_(user_ids))
        )

        print(f"DELETE users: {result.rowcount}")

    # Independent verification after commit.
    async with engine.connect() as conn:
        remaining = await find_users(conn)

    if remaining:
        print("\nWARNING: matching Locust users still remain:")
        for user in remaining:
            print(f"  {user.handle}")
        raise SystemExit(3)

    print("\nCleanup complete.")
    print("Matching Locust users remaining: 0")


if __name__ == "__main__":
    asyncio.run(main())
