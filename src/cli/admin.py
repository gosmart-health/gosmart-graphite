"""Administrative CLI utility for GoSmart DICOM Graph Server.

Provides out-of-band disaster recovery and administrative capabilities:
- Reset forgotten administrator passwords
- Create initial/additional Platform Administrators
- List platform administrators
"""

import argparse
import asyncio
import getpass
import sys
from typing import Optional

from db.neo4j import neo4j_client
from service.group_service import group_service
from service.user_service import user_service


async def cmd_list_admins() -> int:
    """List all users possessing PLATFORM_ADMIN system role."""
    await neo4j_client.connect()
    try:
        admins = await user_service.list_platform_admins(include_inactive=True)
        if not admins:
            print("No Platform Administrators found in database.")
            return 0

        print(f"\nFound {len(admins)} Platform Administrator(s):")
        print("-" * 75)
        print(f"{'User ID':<24} | {'Email':<30} | {'Status':<8} | {'Display Name'}")
        print("-" * 75)
        for adm in admins:
            status = "ACTIVE" if adm.get("is_active", True) else "INACTIVE"
            print(
                f"{adm.get('id', ''):<24} | "
                f"{adm.get('primary_email', ''):<30} | "
                f"{status:<8} | "
                f"{adm.get('display_name', '')}"
            )
        print("-" * 75 + "\n")
        return 0
    finally:
        await neo4j_client.close()


async def cmd_reset_password(
    identifier: str,
    password: Optional[str] = None,
    acted_by: str = "cli:admin",
) -> int:
    """Reset password for a user out-of-band."""
    if not password:
        password = getpass.getpass(f"Enter new password for '{identifier}': ")
        confirm = getpass.getpass("Confirm new password: ")
        if password != confirm:
            print("Error: Passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < 8:
        print("Error: Password must be at least 8 characters long.", file=sys.stderr)
        return 1

    await neo4j_client.connect()
    try:
        success = await user_service.reset_admin_password(
            identifier=identifier,
            new_password=password,
            acted_by=acted_by,
        )
        if success:
            print(f"Successfully reset password for '{identifier}'.")
            return 0
        else:
            print(f"Error: User '{identifier}' not found.", file=sys.stderr)
            return 1
    finally:
        await neo4j_client.close()


async def cmd_create_admin(
    username: str,
    email: str,
    password: Optional[str] = None,
    display_name: Optional[str] = None,
    acted_by: str = "cli:admin",
) -> int:
    """Create a new Platform Administrator user and affiliate with Catch-All group."""
    if not password:
        password = getpass.getpass(f"Enter password for admin '{username}' ({email}): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < 8:
        print("Error: Password must be at least 8 characters long.", file=sys.stderr)
        return 1

    await neo4j_client.connect()
    try:
        # Check if email already exists
        existing = await user_service.get_user_by_email(email)
        if existing:
            print(f"Error: User with email '{email}' already exists (ID: {existing['id']}).", file=sys.stderr)
            return 1

        catch_all = await group_service.get_or_create_catch_all_group(acted_by=acted_by)
        created = await user_service.create_local_user(
            username=username,
            email=email,
            password=password,
            display_name=display_name or username,
            system_roles=["USER", "PLATFORM_ADMIN"],
            group_id=catch_all.get("id"),
            group_roles=["Admin"],
            acted_by=acted_by,
        )
        print("Successfully created Platform Administrator:")
        print(f"  User ID:      {created['id']}")
        print(f"  Username:     {username}")
        print(f"  Email:        {email}")
        print(f"  Display Name: {created.get('display_name')}")
        print(f"  Roles:        {created.get('system_roles')}")
        return 0
    finally:
        await neo4j_client.close()


async def cmd_create_api_key(
    identifier: str,
    name: str = "CLI Operator Key",
    scopes: Optional[str] = "*",
    expires_days: Optional[int] = None,
    acted_by: str = "cli:admin",
) -> int:
    """Generate an API key for a specified user out-of-band."""
    from datetime import datetime, timedelta, timezone

    await neo4j_client.connect()
    try:
        user = await user_service.get_user_by_email(identifier)
        if not user:
            user = await user_service.get_user_by_id(identifier)
        if not user:
            print(f"Error: User '{identifier}' not found.", file=sys.stderr)
            return 1

        scope_list = [s.strip() for s in scopes.split(",")] if scopes else ["*"]
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None

        result = await user_service.create_api_key(
            user_id=user["id"],
            name=name,
            scopes=scope_list,
            expires_at=expires_at,
            acted_by=acted_by,
        )

        print("\nSuccessfully generated API key:")
        print("-" * 65)
        print(f"  User ID:      {user['id']}")
        print(f"  User Email:   {user.get('primary_email')}")
        print(f"  Key Name:     {name}")
        print(f"  Scopes:       {scope_list}")
        print(f"  Expires At:   {expires_at.isoformat() if expires_at else 'Never'}")
        print(f"  API Key:      {result['raw_api_key']}")
        print("-" * 65)
        print("Important: Store this API key securely. It will not be shown again.\n")
        return 0
    finally:
        await neo4j_client.close()


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="GoSmart DICOM Graph Server - Administrative Management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommand to execute")

    # list-admins
    subparsers.add_parser("list-admins", help="List all Platform Administrators")

    # reset-password
    p_reset = subparsers.add_parser("reset-password", help="Reset password for an existing user")
    p_reset.add_argument("--email", "-e", required=True, help="User primary email or User ID")
    p_reset.add_argument("--password", "-p", default=None, help="New plaintext password (prompts if omitted)")
    p_reset.add_argument("--acted-by", default="cli:admin", help="Audit actor identifier")

    # create-admin
    p_create = subparsers.add_parser("create-admin", help="Create a new Platform Administrator")
    p_create.add_argument("--username", "-u", required=True, help="Unique username")
    p_create.add_argument("--email", "-e", required=True, help="Primary email address")
    p_create.add_argument("--password", "-p", default=None, help="Password (prompts if omitted)")
    p_create.add_argument("--display-name", "-d", default=None, help="Display name")
    p_create.add_argument("--acted-by", default="cli:admin", help="Audit actor identifier")

    # create-api-key
    p_apikey = subparsers.add_parser("create-api-key", help="Generate an API key for a user")
    p_apikey.add_argument("--email", "-e", required=True, help="User primary email or User ID")
    p_apikey.add_argument("--name", "-n", default="CLI Operator Key", help="Name/description for the API key")
    p_apikey.add_argument("--scopes", "-s", default="*", help="Comma-separated scopes (default: '*')")
    p_apikey.add_argument("--expires-days", type=int, default=None, help="Expiration in days (default: never)")
    p_apikey.add_argument("--acted-by", default="cli:admin", help="Audit actor identifier")

    args = parser.parse_args()

    if args.command == "list-admins":
        exit_code = asyncio.run(cmd_list_admins())
    elif args.command == "reset-password":
        exit_code = asyncio.run(
            cmd_reset_password(
                identifier=args.email,
                password=args.password,
                acted_by=args.acted_by,
            )
        )
    elif args.command == "create-admin":
        exit_code = asyncio.run(
            cmd_create_admin(
                username=args.username,
                email=args.email,
                password=args.password,
                display_name=args.display_name,
                acted_by=args.acted_by,
            )
        )
    elif args.command == "create-api-key":
        exit_code = asyncio.run(
            cmd_create_api_key(
                identifier=args.email,
                name=args.name,
                scopes=args.scopes,
                expires_days=args.expires_days,
                acted_by=args.acted_by,
            )
        )
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
