"""Deleting an account, and exporting its data.

Deleting removes everything the user owns, in an order that is safe to repeat if it is interrupted:

    1. revoke the Google grant at Google, then forget the token here (this stops the inbox check and the daily
       summary for them at once), and sign them out everywhere;
    2. their projects: vectors, uploaded files, chunks, asset records, the project records;
    3. everything else that carries their user id (see USER_ID_COLLECTIONS), their conversation threads, their
       rate-limit counters, their open stream tickets;
    4. the account itself;
    5. a second pass over steps 2-3 for anything a job that was already running wrote in the meantime.

Anything NOT listed here would be left behind, so tests/test_account.py checks every collection in the database
against this module (a new collection that stores user data must be added here, or the test fails), and
scripts/verify_account_deleted.py searches the whole database for the user's id afterwards.

The audit log (`actions_log`) is deleted too. It is append-only for the application during normal use; an account
erasure is the one explicit exception, requested by the user for their own data.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from typing import Optional

import httpx
from bson import ObjectId

from controllers.ProjectController import ProjectController
from helpers import event_bus
from helpers.config import get_settings
from helpers.privacy import mask_email
from models.SessionModel import SessionModel
from models.UserModel import UserModel

logger = logging.getLogger("uvicorn.error")

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Collections whose documents carry the user's id in `user_id`.
USER_ID_COLLECTIONS = (
    "tasks", "task_email_scans", "reminders", "queries", "jobs", "notifications", "push_subscriptions",
    "actions_log", "usage", "sessions",
)
# The agent's saved conversations: `thread_id` is "<user id>_<thread name>".
CHECKPOINT_COLLECTIONS = ("lg_checkpoints", "lg_checkpoint_blobs", "lg_checkpoint_writes")
# Handled by their own steps (projects and what hangs off them, the account, counters keyed by id or address).
HANDLED_ELSEWHERE = ("projects", "assets", "chunks", "users", "rate_limits")
# Collections that exist but store nothing about a user today (no user id field, no feature writes to them).
UNUSED_COLLECTIONS = ("emails", "meetings")


# ── finding and removing ────────────────────────────────────────────────────

def _prefix(user_id: str) -> str:
    return f"u{user_id}p"


async def _project_ids(db, user_id: str) -> list[tuple]:
    """(Mongo _id, storage id) of each of the user's projects."""
    cursor = db["projects"].find({"project_id": {"$regex": f"^{re.escape(_prefix(user_id))}"}}, {"project_id": 1})
    return [(doc["_id"], doc["project_id"]) async for doc in cursor]


def _project_folders(user_id: str) -> list[str]:
    files_dir = ProjectController().files_dir
    if not os.path.isdir(files_dir):
        return []
    return [os.path.join(files_dir, name) for name in os.listdir(files_dir) if name.startswith(_prefix(user_id))]


async def _remove_projects(services, user_id: str, removed: dict) -> None:
    db = services.mongo_db
    for project_oid, project_id in await _project_ids(db, user_id):
        try:
            await asyncio.to_thread(services.vectordb_client.delete_collection, f"collection_{project_id}")
            removed["vector_collections"] = removed.get("vector_collections", 0) + 1
        except Exception:
            logger.error("Could not delete a vector collection during account deletion", exc_info=True)
            removed["vector_errors"] = removed.get("vector_errors", 0) + 1
        removed["chunks"] = removed.get("chunks", 0) + (await db["chunks"].delete_many({"chunk_project_id": project_oid})).deleted_count
        removed["assets"] = removed.get("assets", 0) + (await db["assets"].delete_many({"asset_project_id": project_oid})).deleted_count
        removed["projects"] = removed.get("projects", 0) + (await db["projects"].delete_one({"_id": project_oid})).deleted_count
    for folder in _project_folders(user_id):
        await asyncio.to_thread(shutil.rmtree, folder, True)
        removed["file_folders"] = removed.get("file_folders", 0) + 1


async def _remove_user_documents(db, user_id: str, email: str, removed: dict) -> None:
    for name in USER_ID_COLLECTIONS:
        count = (await db[name].delete_many({"user_id": user_id})).deleted_count
        removed[name] = removed.get(name, 0) + count
    thread_prefix = {"$regex": f"^{re.escape(user_id)}_"}
    for name in CHECKPOINT_COLLECTIONS:
        count = (await db[name].delete_many({"thread_id": thread_prefix})).deleted_count
        removed[name] = removed.get(name, 0) + count
    # Rate-limit counters are keyed by the user id ("chat:<id>") or a hash of the address ("login:<hash>:<ip>"). Counters
    # made before the address was hashed carried the address itself, so those are matched too.
    keys = [{"_id": {"$regex": re.escape(user_id)}}]
    if email:
        from routes.local_auth import login_key_for

        keys.append({"_id": {"$regex": f"^login:{login_key_for(email)}:"}})
        keys.append({"_id": {"$regex": re.escape(email), "$options": "i"}})
    removed["rate_limits"] = removed.get("rate_limits", 0) + (await db["rate_limits"].delete_many({"$or": keys})).deleted_count
    event_bus.forget_user(user_id)


async def revoke_google_token(token: Optional[dict]) -> str:
    """Tell Google to cancel the grant. "revoked", "failed" or "not_connected". Never raises."""
    if not token:
        return "not_connected"
    secret = token.get("refresh_token") or token.get("token")
    if not secret:
        return "not_connected"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(GOOGLE_REVOKE_URL, data={"token": secret})
        # 400 means Google no longer knows the token (already revoked or expired): nothing is left to revoke.
        return "revoked" if response.status_code in (200, 400) else "failed"
    except Exception:
        logger.error("Could not reach Google to revoke the grant", exc_info=True)
        return "failed"


async def delete_account(services, user: dict) -> dict:
    """Delete the account and everything it owns. Returns a summary: what was removed, whether Google was told."""
    db = services.mongo_db
    user_id, email = str(user["_id"]), user.get("email", "")
    users = UserModel(db)
    removed: dict = {}

    token = await users.get_google_token(email) if email else None
    google = await revoke_google_token(token)
    await users.update_google_token(email, None)                      # no background job can act for them any more
    await SessionModel(db).revoke_all_for_user(user_id)

    await _remove_projects(services, user_id, removed)
    await _remove_user_documents(db, user_id, email, removed)
    removed["users"] = (await db["users"].delete_one({"_id": ObjectId(user_id)})).deleted_count

    # A job that was already running may have written something since the first pass.
    second: dict = {}
    await _remove_projects(services, user_id, second)
    await _remove_user_documents(db, user_id, email, second)
    swept = sum(second.get(name, 0) for name in (*USER_ID_COLLECTIONS, *CHECKPOINT_COLLECTIONS, "chunks", "assets", "projects"))
    if swept:
        logger.warning(f"Account deletion for {mask_email(email)}: {swept} document(s) written during the deletion were removed")

    logger.info(f"Account deleted for {mask_email(email)}: Google access {google}")
    return {"deleted": True, "google_access": google, "removed": {k: v for k, v in removed.items() if v},
            "swept_afterwards": swept}


async def scan_for_user(db, user_id: str, email: str = "") -> dict:
    """Every collection that still holds a document mentioning the user's id (or address) anywhere in it.
    Reads the whole database, so it is for verification (the script and the tests), not for requests."""
    needles = [user_id.lower()] + ([email.lower()] if email else [])
    found: dict = {}
    for name in await db.list_collection_names():
        count = 0
        async for doc in db[name].find({}):
            text = json.dumps(doc, default=str).lower()
            if any(needle in text for needle in needles):
                count += 1
        if count:
            found[name] = count
    return found


async def orphaned_project_data(db) -> dict:
    """Chunks and asset records whose project no longer exists. They carry no user id (they point at their project),
    but a chunk holds the text of a user's document, so after a deletion none may be left without a project."""
    project_ids = set(await db["projects"].distinct("_id"))
    orphans = {}
    for name, field in (("chunks", "chunk_project_id"), ("assets", "asset_project_id")):
        count = 0
        for owner in await db[name].distinct(field):
            if owner not in project_ids:
                count += await db[name].count_documents({field: owner})
        if count:
            orphans[name] = count
    return orphans


# ── export ──────────────────────────────────────────────────────────────────

README_FILE = os.path.join(os.path.dirname(__file__), "export_readme.txt")   # the note placed inside every export


def _json_default(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, bytes):
        return None
    return str(value)


def _clean(docs: list[dict], drop: tuple = ()) -> list[dict]:
    return [{k: v for k, v in doc.items() if k not in drop} for doc in docs]


async def _find(db, name: str, query: dict, sort=None, limit: int = 100000) -> list[dict]:
    cursor = db[name].find(query)
    if sort:
        cursor = cursor.sort(sort)
    return [doc async for doc in cursor.limit(limit)]


async def _conversations(services, user_id: str) -> list[dict]:
    checkpointer = getattr(services, "checkpointer", None)
    if checkpointer is None:
        return []
    threads = await checkpointer.checkpoints.distinct("thread_id", {"thread_id": {"$regex": f"^{re.escape(user_id)}_"}})
    conversations = []
    for thread_id in sorted(threads):
        try:
            found = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            history = ((found.checkpoint or {}).get("channel_values") or {}).get("chat_history") or [] if found else []
        except Exception:
            logger.error("Could not read a conversation for the export", exc_info=True)
            history = []
        conversations.append({"thread": thread_id[len(user_id) + 1:], "messages": history})
    return conversations


async def collect_export(services, user: dict) -> tuple[dict, list[tuple]]:
    """(the JSON-able export, the files to add: (path on disk, name inside the zip))."""
    db = services.mongo_db
    user_id = str(user["_id"])
    projects, files = [], []
    for project_oid, project_id in await _project_ids(db, user_id):
        name = project_id[len(_prefix(user_id)):]
        assets = await _find(db, "assets", {"asset_project_id": project_oid})
        folder = os.path.join(ProjectController().files_dir, project_id)
        listed = []
        if os.path.isdir(folder):
            for file_name in sorted(os.listdir(folder)):
                path = os.path.join(folder, file_name)
                if os.path.isfile(path) and not os.path.islink(path):
                    listed.append({"name": file_name, "bytes": os.path.getsize(path)})
                    files.append((path, f"files/{name}/{file_name}"))
        projects.append({"name": name, "files": listed,
                         "records": _clean(assets, drop=("asset_project_id",))})

    settings = get_settings()
    data = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "app": settings.APP_NAME,
        "account": {
            "id": user_id,
            "name": user.get("name"), "email": user.get("email"), "timezone": user.get("timezone"),
            "email_verified": user.get("email_verified") is True,
            "google_connected": bool(user.get("google_token")),
            "notification_preferences": user.get("notification_prefs") or {},
            "created_at": user.get("created_at"),
        },
        "reminders": _clean(await _find(db, "reminders", {"user_id": user_id}, [("created_at", 1)]), drop=("user_id",)),
        "tasks": _clean(await _find(db, "tasks", {"user_id": user_id}), drop=("user_id", "description_key")),
        "notifications": _clean(await _find(db, "notifications", {"user_id": user_id}, [("created_at", 1)]), drop=("user_id", "dedupe_key")),
        "action_log": _clean(await _find(db, "actions_log", {"user_id": user_id}, [("created_at", 1)]), drop=("user_id",)),
        "usage": _clean(await _find(db, "usage", {"user_id": user_id}, [("day", 1)]), drop=("user_id",)),
        "chat_requests": _clean(await _find(db, "queries", {"user_id": user_id}, [("created_at", 1)]), drop=("user_id",)),
        "push_browsers": await db["push_subscriptions"].count_documents({"user_id": user_id}),
        "conversations": await _conversations(services, user_id),
        "projects": projects,
    }
    return data, files


def _write_zip(path: str, data: dict, files: list[tuple]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        with open(README_FILE, encoding="utf-8") as note:
            readme = note.read()
        archive.writestr("nexusai-export/export.json", json.dumps(data, default=_json_default, ensure_ascii=False, indent=2))
        archive.writestr("nexusai-export/README.txt", readme)
        for source, arcname in files:
            archive.write(source, f"nexusai-export/{arcname}")


async def build_export(services, user: dict) -> str:
    """Write the user's export to a temporary ZIP file and return its path (the caller deletes it)."""
    data, files = await collect_export(services, user)
    handle, path = tempfile.mkstemp(suffix=".zip", prefix="nexusai-export-")
    os.close(handle)
    try:
        await asyncio.to_thread(_write_zip, path, data, files)
    except Exception:
        os.remove(path)
        raise
    return path
