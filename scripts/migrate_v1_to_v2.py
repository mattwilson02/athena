#!/usr/bin/env python3
"""Migrate vault from V1 flat folder structure to V2 domain-grouped structure.

V1: vault/Goals/get-promoted.md
V2: vault/Self/Goals/get-promoted.md

This script:
1. Creates the V2 directory structure
2. Moves existing files into their new domain-grouped folders
3. Node IDs and wikilinks are unchanged (IDs don't include folder paths)

Usage:
    python scripts/migrate_v1_to_v2.py [--dry-run]
"""

import os
import shutil
import sys

VAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vault")

# V1 folder → V2 folder mapping
FOLDER_MAP = {
    "Goals": "Self/Goals",
    "Fears": "Self/Fears",
    "Beliefs": "Self/Beliefs",
    "Values": "Self/Values",
    "Habits": "Self/Habits",
    "Skills": "Self/Skills",
    "People": "People/Persons",
    "Books": "Knowledge/Books",
    "Interests": "Knowledge/Interests",
    "Experiences": "Life/Experiences",
    "Daily": "Life/Daily",
}

# New V2 directories that don't exist in V1
NEW_DIRS = [
    "People/Organisations",
    "Knowledge/Articles",
    "Knowledge/Ideas",
    "Knowledge/Notes",
    "Life/Memories",
    "Planning/Tasks",
    "Planning/Projects",
    "Planning/Reminders",
    "Planning/Events",
    "Places",
    "Finance/Expenses",
    "Finance/Subscriptions",
    "Finance/Budgets",
]


def migrate(dry_run: bool = False) -> None:
    vault = os.path.abspath(VAULT_PATH)
    if not os.path.isdir(vault):
        print(f"Vault not found at {vault}")
        sys.exit(1)

    print(f"Migrating vault at: {vault}")
    if dry_run:
        print("(DRY RUN — no files will be moved)\n")

    # Step 1: Create all new directories
    all_dirs = list(FOLDER_MAP.values()) + NEW_DIRS
    for d in all_dirs:
        full = os.path.join(vault, d)
        if not os.path.exists(full):
            print(f"  CREATE DIR  {d}/")
            if not dry_run:
                os.makedirs(full, exist_ok=True)

    # Step 2: Move files from V1 folders to V2 folders
    moved = 0
    for v1_folder, v2_folder in FOLDER_MAP.items():
        v1_path = os.path.join(vault, v1_folder)
        v2_path = os.path.join(vault, v2_folder)

        if not os.path.isdir(v1_path):
            continue

        for filename in os.listdir(v1_path):
            if not filename.endswith(".md"):
                continue

            src = os.path.join(v1_path, filename)
            dst = os.path.join(v2_path, filename)

            if os.path.exists(dst):
                print(f"  SKIP (exists) {v1_folder}/{filename} → {v2_folder}/{filename}")
                continue

            print(f"  MOVE  {v1_folder}/{filename} → {v2_folder}/{filename}")
            if not dry_run:
                os.makedirs(v2_path, exist_ok=True)
                shutil.move(src, dst)
            moved += 1

    # Step 3: Clean up empty V1 directories
    for v1_folder in FOLDER_MAP:
        v1_path = os.path.join(vault, v1_folder)
        if not os.path.isdir(v1_path):
            continue

        # Only remove if empty (all .md files moved out)
        remaining = [f for f in os.listdir(v1_path) if not f.startswith(".")]
        if not remaining:
            print(f"  REMOVE DIR  {v1_folder}/ (empty)")
            if not dry_run:
                shutil.rmtree(v1_path)
        else:
            print(f"  KEEP DIR    {v1_folder}/ ({len(remaining)} files remaining)")

    print(f"\nDone. Moved {moved} files.")
    if dry_run:
        print("Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    migrate(dry_run=dry_run)
