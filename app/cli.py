"""Admin commands.

    python -m app.cli create-user --email EMAIL --password PASSWORD [--must-change-password]
    python -m app.cli migrate-supabase-users --supabase-url URL --service-role-key KEY [--output creds.csv]
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime
from uuid import UUID

import httpx

from app.database import SessionLocal
from app.services.access import normalize_email
from app.services.users import get_user_by_email, new_user, validate_email

SUPABASE_PAGE_SIZE = 1000


async def create_user(args: argparse.Namespace) -> int:
    email = validate_email(args.email)
    async with SessionLocal() as db:
        if await get_user_by_email(db, email):
            print(f"User {email} already exists")
            return 1
        user, _ = new_user(email, args.password, must_change_password=args.must_change_password)
        db.add(user)
        await db.commit()
        print(f"Created user {email} ({user.id})")
    return 0


def fetch_supabase_users(supabase_url: str, service_role_key: str) -> list[dict]:
    headers = {"apikey": service_role_key, "Authorization": f"Bearer {service_role_key}"}
    users: list[dict] = []
    page = 1
    with httpx.Client(timeout=20) as client:
        while True:
            response = client.get(
                f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
                headers=headers,
                params={"page": page, "per_page": SUPABASE_PAGE_SIZE},
            )
            response.raise_for_status()
            batch = response.json().get("users", [])
            users.extend(batch)
            if len(batch) < SUPABASE_PAGE_SIZE:
                return users
            page += 1


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def migrate_supabase_users(args: argparse.Namespace) -> int:
    supabase_users = fetch_supabase_users(args.supabase_url, args.service_role_key)
    print(f"Fetched {len(supabase_users)} users from Supabase")

    created: list[tuple[str, str]] = []
    async with SessionLocal() as db:
        for supabase_user in supabase_users:
            raw_email = supabase_user.get("email")
            if not raw_email:
                continue
            email = normalize_email(raw_email)
            if await get_user_by_email(db, email):
                print(f"skip   {email} (already exists)")
                continue
            # Keep the Supabase id so farm_memberships.user_id bindings stay valid.
            user, temporary_password = new_user(
                email,
                None,
                user_id=UUID(supabase_user["id"]),
                created_at=_parse_timestamp(supabase_user.get("created_at")),
            )
            db.add(user)
            created.append((email, temporary_password or ""))
            print(f"create {email}")
        await db.commit()

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "temporary_password"])
            writer.writerows(created)
        print(f"Wrote {len(created)} temporary passwords to {args.output}")
    else:
        print()
        print("email,temporary_password")
        for email, temporary_password in created:
            print(f"{email},{temporary_password}")
    print(f"Created {len(created)} users; they must change their password on first login.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-user", help="Create a local user account")
    create.add_argument("--email", required=True)
    create.add_argument("--password", required=True)
    create.add_argument(
        "--must-change-password",
        action="store_true",
        help="Force the user to set a new password on first login",
    )
    create.set_defaults(func=create_user)

    migrate = commands.add_parser(
        "migrate-supabase-users",
        help="Copy Supabase Auth users into the local users table with temporary passwords",
    )
    migrate.add_argument("--supabase-url", required=True)
    migrate.add_argument("--service-role-key", required=True)
    migrate.add_argument("--output", help="CSV file to write email,temporary_password rows to")
    migrate.set_defaults(func=migrate_supabase_users)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
