"""Prove that nothing about a deleted account remains.

    python scripts/verify_account_deleted.py USER_ID [--email user@example.com]

Searches EVERY collection of the database named in src/.env for the user's id (and address, if given) anywhere inside
any document, looks for the user's upload folders, and looks for the user's vector collections. Prints what it
finds and exits 0 only if there is nothing, so it can be used in a runbook or a test. Read-only.

It reads the whole database, so run it on demand, not on a schedule. The vector store is checked on disk, so it works
while the server is running (the embedded Qdrant folder cannot be opened by a second process).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from pymongo import MongoClient  # noqa: E402

from helpers.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("user_id", help="the id of the deleted user (24 hex characters)")
    parser.add_argument("--email", default="", help="also search for this address")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{24}", args.user_id):
        print("USER_ID must be 24 hex characters, as in the users collection")
        return 2

    settings = get_settings()
    db = MongoClient(settings.MONGODB_URL)[settings.MONGODB_DATABASE]
    needles = [args.user_id.lower()] + ([args.email.lower()] if args.email else [])
    problems = []

    scanned = 0
    for name in sorted(db.list_collection_names()):
        hits = 0
        for doc in db[name].find({}):
            scanned += 1
            text = json.dumps(doc, default=str).lower()
            if any(needle in text for needle in needles):
                hits += 1
        if hits:
            problems.append(f"collection {name}: {hits} document(s) mention the user")

    # Chunks (the text of uploaded documents) and asset records name their project, not the user, so a text search
    # cannot find them: instead check that none is left without a project.
    project_ids = set(db["projects"].distinct("_id"))
    for name, field in (("chunks", "chunk_project_id"), ("assets", "asset_project_id")):
        orphaned = sum(db[name].count_documents({field: owner}) for owner in db[name].distinct(field) if owner not in project_ids)
        if orphaned:
            problems.append(f"collection {name}: {orphaned} document(s) belong to a project that no longer exists")

    files_dir = SRC / "assets" / "files"
    prefix = f"u{args.user_id}p"
    if files_dir.is_dir():
        for entry in os.listdir(files_dir):
            if entry.startswith(prefix):
                problems.append(f"upload folder still exists: {entry}")

    vectors_dir = SRC / "assets" / "database" / settings.VECTOR_DB_PATH / "collection"
    if vectors_dir.is_dir():
        for entry in os.listdir(vectors_dir):
            if entry.startswith(f"collection_{prefix}"):
                problems.append(f"vector collection still exists: {entry}")

    print(f"Searched {scanned} documents in {len(db.list_collection_names())} collections of '{settings.MONGODB_DATABASE}', "
          f"the upload folders and the vector store.")
    if problems:
        print("NOT clean:")
        for line in problems:
            print("  -", line)
        return 1
    print(f"Clean: no document, file or vector for user {args.user_id} remains.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
