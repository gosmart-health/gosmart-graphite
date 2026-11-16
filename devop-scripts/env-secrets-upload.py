#!/usr/bin/env python3
"""
Sync local .env environment variables and secrets to GitHub Actions.

Features:
- Smart-diff: Compares local values with current values in GitHub.
- Variables: Updates only new or modified variables; skips unchanged ones.
- Secrets: Detects existing secrets in GitHub and uploads missing or updated secrets.
- Supports targeting repository-level or deployment environment (via --env).
- Supports dry-run inspection (--dry-run).
- Supports separate env and secret files (--env-file and --secrets-file).
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

DEFAULT_SECRET_KEYWORDS = {"PASSWORD", "SECRET", "KEY", "TOKEN", "CREDENTIALS"}
DEFAULT_SECRET_EXPLICIT_KEYS = {"GOSMART_GS_DB_USER"}


def check_gh_installed() -> None:
    """Verify that gh CLI is installed and authenticated."""
    if not shutil.which("gh"):
        print("❌ Error: GitHub CLI ('gh') is not installed or not in PATH.", file=sys.stderr)
        print("Install it with 'brew install gh' or follow https://cli.github.com/", file=sys.stderr)
        sys.exit(1)

    res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if res.returncode != 0:
        print("❌ Error: GitHub CLI is not authenticated.", file=sys.stderr)
        print("Please run 'gh auth login' before running this script.", file=sys.stderr)
        sys.exit(1)


def parse_env_file(filepath: Path) -> Dict[str, str]:
    """Parse a dotenv file into key-value pairs."""
    entries: Dict[str, str] = {}
    if not filepath.exists():
        return entries

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            # Strip outer quotes if present
            if len(val) >= 2 and (
                (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))
            ):
                val = val[1:-1]

            entries[key] = val

    return entries


def get_github_variables(target_env: Optional[str] = None) -> Dict[str, str]:
    """Fetch existing GitHub repository or environment variables and their values."""
    cmd = ["gh", "variable", "list", "--json", "name,value"]
    if target_env:
        cmd.extend(["--env", target_env])

    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return {item["name"]: item["value"] for item in data}


def get_github_secrets(target_env: Optional[str] = None) -> Set[str]:
    """Fetch existing GitHub repository or environment secret names."""
    cmd = ["gh", "secret", "list", "--json", "name"]
    if target_env:
        cmd.extend(["--env", target_env])

    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return {item["name"] for item in data}


def classify_entries(
    env_entries: Dict[str, str],
    secrets_entries: Dict[str, str],
    secret_keywords: Set[str],
    explicit_secret_keys: Set[str],
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Separate entries into variables and secrets dictionaries."""
    variables: Dict[str, str] = {}
    secrets: Dict[str, str] = {}

    # All entries from dedicated secrets file are classified as secrets
    for k, v in secrets_entries.items():
        secrets[k] = v

    for k, v in env_entries.items():
        if k in secrets:
            continue
        is_secret = k in explicit_secret_keys or any(kw in k.upper() for kw in secret_keywords)
        if is_secret:
            secrets[k] = v
        else:
            variables[k] = v

    return variables, secrets


def sync_variables(
    variables: Dict[str, str],
    remote_vars: Dict[str, str],
    target_env: Optional[str],
    dry_run: bool,
) -> Tuple[int, int, int]:
    """Upload or update variables based on smart-diff against GitHub."""
    created, updated, skipped = 0, 0, 0

    print("\n📦 --- Syncing GitHub Variables ---")
    for key, val in variables.items():
        if key in remote_vars:
            if remote_vars[key] == val:
                print(f"  ✅ [UP-TO-DATE] {key}")
                skipped += 1
                continue
            else:
                print(f"  🔄 [UPDATE]     {key} (Remote: '{remote_vars[key]}' -> Local: '{val}')")
                updated += 1
        else:
            print(f"  ➕ [CREATE]     {key} = '{val}'")
            created += 1

        if not dry_run:
            cmd = ["gh", "variable", "set", key, "--body", val]
            if target_env:
                cmd.extend(["--env", target_env])
            subprocess.run(cmd, check=True)

    return created, updated, skipped


def sync_secrets(
    secrets: Dict[str, str],
    remote_secrets: Set[str],
    target_env: Optional[str],
    overwrite_existing: bool,
    dry_run: bool,
) -> Tuple[int, int, int]:
    """Upload or update secrets based on smart-diff against GitHub."""
    created, updated, skipped = 0, 0, 0

    print("\n🔐 --- Syncing GitHub Secrets ---")
    for key, val in secrets.items():
        exists_remote = key in remote_secrets

        if exists_remote:
            if not overwrite_existing:
                print(f"  ⏩ [SKIP]       {key} (Already exists in GitHub; use --overwrite-secrets to update)")
                skipped += 1
                continue
            print(f"  🔄 [UPDATE]     {key}")
            updated += 1
        else:
            print(f"  ➕ [CREATE]     {key}")
            created += 1

        if not dry_run:
            cmd = ["gh", "secret", "set", key, "--body", val]
            if target_env:
                cmd.extend(["--env", target_env])
            subprocess.run(cmd, check=True)

    return created, updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart-sync local environment variables and secrets to GitHub Actions."
    )
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    parser.add_argument(
        "--env-file",
        type=Path,
        default=repo_root / ".env",
        help="Path to the primary .env file (default: repo root .env)",
    )
    parser.add_argument(
        "--secrets-file",
        type=Path,
        default=None,
        help="Optional path to an explicit secrets file (e.g. .env.secrets)",
    )
    parser.add_argument(
        "--env",
        dest="target_env",
        default=None,
        help="Target a specific GitHub deployment environment (default: repository level)",
    )
    parser.add_argument(
        "--overwrite-secrets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Overwrite secrets if they already exist in GitHub (default: True)",
    )
    parser.add_argument(
        "--skip-secrets",
        action="store_true",
        help="Only sync non-sensitive variables, skip secrets",
    )
    parser.add_argument(
        "--skip-variables",
        action="store_true",
        help="Only sync secrets, skip non-sensitive variables",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect changes without modifying GitHub variables or secrets",
    )

    args = parser.parse_args()

    check_gh_installed()

    if not args.env_file.exists():
        print(f"❌ Error: Primary environment file not found at: {args.env_file}", file=sys.stderr)
        sys.exit(1)

    print(f"🔍 Reading environment from: {args.env_file}")
    env_entries = parse_env_file(args.env_file)

    secrets_entries: Dict[str, str] = {}
    if args.secrets_file:
        if not args.secrets_file.exists():
            print(f"❌ Error: Secrets file not found at: {args.secrets_file}", file=sys.stderr)
            sys.exit(1)
        print(f"🔍 Reading dedicated secrets from: {args.secrets_file}")
        secrets_entries = parse_env_file(args.secrets_file)

    variables, secrets = classify_entries(
        env_entries,
        secrets_entries,
        DEFAULT_SECRET_KEYWORDS,
        DEFAULT_SECRET_EXPLICIT_KEYS,
    )

    scope_name = f"Environment '{args.target_env}'" if args.target_env else "Repository-level"
    print(f"🌐 Target GitHub Scope: {scope_name}")
    if args.dry_run:
        print("⚠️  DRY-RUN MODE: No changes will be applied to GitHub.")

    # Fetch current GitHub state
    print("📡 Querying current GitHub state...")
    remote_vars = get_github_variables(args.target_env)
    remote_secrets = get_github_secrets(args.target_env)
    print(f"   Found {len(remote_vars)} variable(s) and {len(remote_secrets)} secret(s) currently in GitHub.")

    var_created, var_updated, var_skipped = 0, 0, 0
    if not args.skip_variables:
        var_created, var_updated, var_skipped = sync_variables(variables, remote_vars, args.target_env, args.dry_run)

    sec_created, sec_updated, sec_skipped = 0, 0, 0
    if not args.skip_secrets:
        sec_created, sec_updated, sec_skipped = sync_secrets(
            secrets,
            remote_secrets,
            args.target_env,
            args.overwrite_existing_secrets if hasattr(args, "overwrite_existing_secrets") else args.overwrite_secrets,
            args.dry_run,
        )

    print("\n✨ --- Sync Summary ---")
    if not args.skip_variables:
        print(f"Variables: {var_created} created, {var_updated} updated, {var_skipped} up-to-date")
    if not args.skip_secrets:
        print(f"Secrets:   {sec_created} created, {sec_updated} updated, {sec_skipped} skipped")

    if args.dry_run:
        print("\nDry-run completed successfully.")
    else:
        print("\nAll synced successfully!")


if __name__ == "__main__":
    main()
