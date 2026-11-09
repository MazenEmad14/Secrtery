"""Account deletion, data export and email verification (PR 0.6).

The deletion tests fill EVERY collection for two users, delete one, and then search the whole database for any trace
of that user (the same search scripts/verify_account_deleted.py does). A guard test fails when a collection exists
that the deletion code does not know about. Real MongoDB (throwaway database); skipped when none is reachable.
"""
import asyncio
import io
import json
import logging
import os
import shutil
import sys
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.ProjectController import ProjectController
from helpers import auth_utils, event_bus
from helpers.config import get_settings
from helpers.error_handlers import install_error_handlers
from models.ActionLogModel import ActionLogModel
from models.AssetModel import AssetModel
from models.ChunkModel import ChunkModel
from models.JobModel import JobModel
from models.NotificationModel import NotificationModel
from models.ProjectModel import ProjectModel
from models.PushSubscriptionModel import PushSubscriptionModel
from models.SessionModel import SessionModel
from models.UsageModel import UsageModel
from models.UserModel import UserModel
from models.db_schemes import Asset, DataChunk
from models.indexes import ALL_MODELS, ensure_all_indexes
from routes import account as account_routes, local_auth, notifications as notification_routes
from routes.local_auth import login_key_for
from services import account as account_service
from services import actions as action_service
from services import mailer
from test_config import make

MONGO_URL = "mongodb://localhost:27017/"
SECRET_PASSWORD = "correct horse battery 42"

pytestmark = pytest.mark.skipif(
    not (lambda: __import__("pymongo").MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info() or True)(),
    reason="MongoDB is not reachable")


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


def run_with_db(coro_fn):
    async def runner():
        conn = AsyncIOMotorClient(MONGO_URL)
        name = f"test_account_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


class FakeVectors:
    def __init__(self):
        self.deleted = []

    def delete_collection(self, collection_name):
        self.deleted.append(collection_name)


class FakeCheckpointer:
    """Just the part the export reads: thread ids and the latest chat history of each."""

    def __init__(self, db):
        self.checkpoints = db["lg_checkpoints"]

    async def aget_tuple(self, config):
        doc = await self.checkpoints.find_one({"thread_id": config["configurable"]["thread_id"]})
        return SimpleNamespace(checkpoint={"channel_values": {"chat_history": doc["history"]}}) if doc else None


def services_for(db, vectors=None):
    return SimpleNamespace(mongo_db=db, vectordb_client=vectors or FakeVectors(), checkpointer=FakeCheckpointer(db))


async def make_user(db, email, password=SECRET_PASSWORD, google=False):
    doc = {"name": email.split("@")[0].title(), "email": email, "timezone": "Africa/Cairo", "email_verified": True,
           "notification_prefs": {"push": True}, "created_at": datetime.utcnow()}
    if password:
        doc["password_hash"] = auth_utils.get_password_hash(password)
    if google:
        doc["google_token"] = {"token": "ya29.SECRET-ACCESS", "refresh_token": "1//SECRET-REFRESH"}
    inserted = await db["users"].insert_one(doc)
    return {**doc, "_id": inserted.inserted_id}


async def fill_everything(db, user, files_made: list) -> dict:
    """Give the user data in every collection (and a folder of uploads). Returns what was made, for the assertions."""
    uid, email = str(user["_id"]), user["email"]
    await db["tasks"].insert_one({"user_id": uid, "project_id": "default", "description": "Send the proposal"})
    await db["task_email_scans"].insert_one({"user_id": uid, "message_id": "m1"})
    await db["reminders"].insert_one({"user_id": uid, "task": "Call Sara", "date": "2026-12-01", "time": "10:00",
                                      "created_at": datetime.utcnow()})
    await db["queries"].insert_one({"user_id": uid, "query_text": "what is open with Ahmed?", "created_at": datetime.utcnow()})
    await JobModel(db).enqueue("process_documents", uid, f"u{uid}pdefault", "default", {}, 1)
    await NotificationModel(db).create(uid, "reminder", "🔔 Reminder: Call Sara", dedupe_key=f"reminder:{uid}")
    await PushSubscriptionModel(db).save(uid, f"https://fcm.googleapis.com/fcm/send/{uid}", "p", "a")
    proposal = await action_service.propose(ActionLogModel(db), uid, "send_email", {"recipient": "a@x.com", "subject": "Hi"})
    await UsageModel(db).add(uid, {"llm_calls": 3, "est_cost_usd": 0.01})
    await SessionModel(db).start(uid, 30)
    # the project, its records, its vectors and its files
    project = await ProjectModel(db).get_project_or_create_one(project_id=f"u{uid}pdefault")
    asset = await AssetModel(db).create_asset(Asset(asset_project_id=project.id, asset_type="file", asset_name="notes.txt", asset_size=11))
    await ChunkModel(db).insert_many_chunks([DataChunk(chunk_text="the vault code is 1234", chunk_metadata={}, chunk_order=1,
                                                       chunk_project_id=project.id, chunk_asset_id=asset.id)])
    folder = os.path.join(ProjectController().files_dir, f"u{uid}pdefault")
    os.makedirs(folder, exist_ok=True)
    Path(folder, "notes.txt").write_text("hello world", encoding="utf-8")
    files_made.append(folder)
    # conversations, rate limits, a stream ticket
    for collection in ("lg_checkpoints", "lg_checkpoint_blobs", "lg_checkpoint_writes"):
        await db[collection].insert_one({"thread_id": f"{uid}_chat1", "checkpoint_ns": "", "checkpoint_id": "1",
                                         "history": [{"role": "user", "content": "hello there"}]})
    await db["rate_limits"].insert_many([
        {"_id": f"chat:{uid}", "count": 1, "expires_at": datetime.utcnow() + timedelta(hours=1)},
        {"_id": f"login:{login_key_for(email)}:127.0.0.1", "count": 1, "expires_at": datetime.utcnow() + timedelta(hours=1)},
        {"_id": f"login:{email}:127.0.0.1", "count": 1, "expires_at": datetime.utcnow() + timedelta(hours=1)},   # the old format
    ])
    event_bus.issue_ticket(uid)
    return {"action_id": proposal["action_id"], "project_id": f"u{uid}pdefault"}


def cleanup(folders):
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)


# ── nothing is forgotten ────────────────────────────────────────────────────

def test_every_collection_the_app_has_is_known_to_the_account_deletion():
    """Add a model, and this fails until the deletion code (or its exceptions) says what to do with its collection."""
    collections = {model.COLLECTION_NAME for model in ALL_MODELS}
    known = (set(account_service.USER_ID_COLLECTIONS) | set(account_service.HANDLED_ELSEWHERE)
             | set(account_service.UNUSED_COLLECTIONS))
    assert collections <= known, f"collections the account deletion does not cover: {sorted(collections - known)}"
    assert set(account_service.UNUSED_COLLECTIONS) <= collections


def test_the_unused_collections_really_store_no_user_data():
    """`emails` and `meetings` have no user id field and no feature writes to them. If that changes, they need
    deleting (and a user field), so this fails when anything starts using them."""
    source_root = Path(__file__).resolve().parents[1]
    users = []
    for path in source_root.rglob("*.py"):
        if "tests" in path.parts or path.name in ("EmailModel.py", "MeetingModel.py", "indexes.py", "account.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "EmailModel" in text or "MeetingModel" in text:
            users.append(str(path.relative_to(source_root)))
    assert users == []


def test_deleting_an_account_leaves_no_trace_of_it_anywhere_and_does_not_touch_anyone_else():
    folders = []

    async def scenario(db):
        alice = await make_user(db, "alice@example.com", google=True)
        bob = await make_user(db, "bob@example.com")
        made_alice = await fill_everything(db, alice, folders)
        await fill_everything(db, bob, folders)
        vectors = FakeVectors()
        bob_before = await account_service.scan_for_user(db, str(bob["_id"]), "bob@example.com")
        alice_before = await account_service.scan_for_user(db, str(alice["_id"]), "alice@example.com")
        # Every collection the app has is among the places the user's data was put (otherwise this test proves little).
        assert set(alice_before) >= {"users", "tasks", "task_email_scans", "reminders", "queries", "jobs", "notifications",
                                      "push_subscriptions", "actions_log", "usage", "sessions", "projects",
                                      "lg_checkpoints", "lg_checkpoint_blobs", "lg_checkpoint_writes", "rate_limits"}
        assert await db["chunks"].count_documents({}) == 2 and await db["assets"].count_documents({}) == 2

        revoked = []

        async def fake_revoke(token):
            revoked.append(token["refresh_token"])
            return "revoked"

        account_service.revoke_google_token, original = fake_revoke, account_service.revoke_google_token
        try:
            summary = await account_service.delete_account(services_for(db, vectors), alice)
        finally:
            account_service.revoke_google_token = original

        assert summary["deleted"] is True and summary["google_access"] == "revoked" and revoked == ["1//SECRET-REFRESH"]
        assert summary["removed"]["users"] == 1 and summary["removed"]["chunks"] == 1 and summary["removed"]["assets"] == 1
        # THE acceptance check: no document anywhere mentions the user, no upload folder, no vectors.
        assert await account_service.scan_for_user(db, str(alice["_id"]), "alice@example.com") == {}
        # chunks and assets name their project, not the user: none may be left over, and Bob's must be.
        assert await account_service.orphaned_project_data(db) == {}
        assert await db["chunks"].count_documents({}) == 1 and await db["assets"].count_documents({}) == 1
        assert not os.path.exists(os.path.join(ProjectController().files_dir, made_alice["project_id"]))
        assert vectors.deleted == [f"collection_{made_alice['project_id']}"]
        assert event_bus.redeem_ticket("anything") is None
        assert not [t for t, (owner, _) in event_bus._tickets.items() if owner == str(alice["_id"])]
        # Bob is exactly as he was.
        assert await account_service.scan_for_user(db, str(bob["_id"]), "bob@example.com") == bob_before
        assert os.path.isdir(os.path.join(ProjectController().files_dir, f"u{bob['_id']}pdefault"))

    try:
        run_with_db(scenario)
    finally:
        cleanup(folders)


def test_deleting_can_be_repeated_after_an_interruption():
    folders = []

    async def scenario(db):
        user = await make_user(db, "carol@example.com")
        await fill_everything(db, user, folders)
        original = account_service._remove_user_documents
        calls = {"n": 0}

        async def failing_once(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("the database went away half-way")
            return await original(*args, **kwargs)

        account_service._remove_user_documents = failing_once
        try:
            with pytest.raises(ConnectionError):
                await account_service.delete_account(services_for(db), user)
            assert await db["users"].count_documents({"_id": user["_id"]}) == 1      # still there, so the user can try again
            assert await db["sessions"].count_documents({"user_id": str(user["_id"])}) == 0    # but signed out everywhere
            summary = await account_service.delete_account(services_for(db), user)
        finally:
            account_service._remove_user_documents = original
        assert summary["deleted"] and await account_service.scan_for_user(db, str(user["_id"]), "carol@example.com") == {}

    try:
        run_with_db(scenario)
    finally:
        cleanup(folders)


def test_something_written_while_the_deletion_ran_is_swept_up_afterwards():
    folders = []

    async def scenario(db):
        user = await make_user(db, "dave@example.com")
        await fill_everything(db, user, folders)
        original = account_service._remove_user_documents
        first = {"done": False}

        async def stray_write_after_first_pass(db_, user_id, email, removed):
            await original(db_, user_id, email, removed)
            if not first["done"]:                       # a job that was already running writes one more thing
                first["done"] = True
                await db_["tasks"].insert_one({"user_id": user_id, "description": "written by a running job"})

        account_service._remove_user_documents = stray_write_after_first_pass
        try:
            summary = await account_service.delete_account(services_for(db), user)
        finally:
            account_service._remove_user_documents = original
        assert summary["swept_afterwards"] >= 1
        assert await account_service.scan_for_user(db, str(user["_id"]), "dave@example.com") == {}

    try:
        run_with_db(scenario)
    finally:
        cleanup(folders)


# ── revoking Google ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, expected", [(200, "revoked"), (400, "revoked"), (500, "failed")])
def test_google_is_told_to_revoke_with_the_refresh_token_and_the_answer_is_reported(monkeypatch, status, expected):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), request.content.decode()
        return httpx.Response(status)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(account_service.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(account_service.revoke_google_token({"token": "access", "refresh_token": "refresh"})) == expected
    assert seen["url"] == account_service.GOOGLE_REVOKE_URL and seen["body"] == "token=refresh"


def test_a_google_outage_or_no_google_does_not_block_deleting_the_account(monkeypatch):
    def broken(request):
        raise httpx.ConnectError("no route")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(account_service.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(broken), **kw))
    assert asyncio.run(account_service.revoke_google_token({"refresh_token": "r"})) == "failed"
    assert asyncio.run(account_service.revoke_google_token(None)) == "not_connected"
    assert asyncio.run(account_service.revoke_google_token({})) == "not_connected"


# ── the routes: delete ──────────────────────────────────────────────────────

def app_for(db, user, with_real_auth=False):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(account_routes.account_router)
    app.include_router(notification_routes.notifications_router)
    app.state.services = services_for(db)
    if not with_real_auth:
        app.dependency_overrides[auth_utils.get_current_user] = lambda: user
    return app


def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_deleting_needs_the_password_and_then_removes_the_account_and_signs_out(monkeypatch):
    folders = []

    async def scenario(db):
        user = await make_user(db, "erin@example.com")
        await fill_everything(db, user, folders)
        async with http(app_for(db, user)) as client:
            none = await client.post("/api/v1/account/delete", json={})
            wrong = await client.post("/api/v1/account/delete", json={"password": "not my password"})
            assert none.status_code == wrong.status_code == 403 and "password" in wrong.json()["detail"]
            assert await db["users"].count_documents({"_id": user["_id"]}) == 1 and await db["reminders"].count_documents({}) == 1

            done = await client.post("/api/v1/account/delete", json={"password": SECRET_PASSWORD})
            assert done.status_code == 200 and done.json()["deleted"] is True
            assert "refresh_token" in done.headers.get("set-cookie", "") or "access_token" in done.headers.get("set-cookie", "")
        assert await account_service.scan_for_user(db, str(user["_id"]), "erin@example.com") == {}

    try:
        run_with_db(scenario)
    finally:
        cleanup(folders)


def test_an_account_without_a_password_confirms_with_its_email_address():
    async def scenario(db):
        user = await make_user(db, "frank@example.com", password=None, google=True)
        async with http(app_for(db, user)) as client:
            wrong = await client.post("/api/v1/account/delete", json={"confirm_email": "someone-else@example.com"})
            assert wrong.status_code == 403 and "email address" in wrong.json()["detail"]
            assert (await client.post("/api/v1/account/delete", json={})).status_code == 403
            ok = await client.post("/api/v1/account/delete", json={"confirm_email": "  FRANK@example.com "})
            assert ok.status_code == 200 and ok.json()["google_access"] in ("revoked", "failed")
        assert await db["users"].count_documents({}) == 0

    account_service.revoke_google_token, original = (lambda token: asyncio.sleep(0, "revoked")), account_service.revoke_google_token
    try:
        run_with_db(scenario)
    finally:
        account_service.revoke_google_token = original


def test_guessing_the_password_for_a_deletion_is_limited(monkeypatch):
    monkeypatch.setattr(get_settings(), "ACCOUNT_DELETE_MAX_PER_HOUR", 2)

    async def scenario(db):
        user = await make_user(db, "gina@example.com")
        async with http(app_for(db, user)) as client:
            assert [(await client.post("/api/v1/account/delete", json={"password": "x"})).status_code for _ in range(2)] == [403, 403]
            blocked = await client.post("/api/v1/account/delete", json={"password": SECRET_PASSWORD})
            assert blocked.status_code == 429 and "minutes" in blocked.json()["detail"]      # even the right one, now
        assert await db["users"].count_documents({"_id": user["_id"]}) == 1

    run_with_db(scenario)


# ── the routes: export ──────────────────────────────────────────────────────

def test_the_export_is_a_zip_with_the_users_own_data_and_files_and_no_secrets():
    folders = []

    async def scenario(db):
        alice = await make_user(db, "alice@example.com", google=True)
        bob = await make_user(db, "bob@example.com")
        await fill_everything(db, alice, folders)
        await fill_everything(db, bob, folders)
        await db["reminders"].insert_one({"user_id": str(bob["_id"]), "task": "BOB'S PRIVATE REMINDER"})
        async with http(app_for(db, alice)) as client:
            response = await client.get("/api/v1/account/export")
        assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
        assert "nexusai-export-" in response.headers["content-disposition"]
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        assert "nexusai-export/export.json" in names and "nexusai-export/README.txt" in names
        assert "nexusai-export/files/default/notes.txt" in names
        assert archive.read("nexusai-export/files/default/notes.txt") == b"hello world"
        data = json.loads(archive.read("nexusai-export/export.json"))
        assert data["account"]["email"] == "alice@example.com" and data["account"]["google_connected"] is True
        assert [r["task"] for r in data["reminders"]] == ["Call Sara"]
        assert data["tasks"][0]["description"] == "Send the proposal"
        assert data["notifications"][0]["title"].startswith("🔔")                              # Unicode survives
        assert data["action_log"][0]["event"] == "proposed" and data["usage"][0]["llm_calls"] == 3
        assert data["projects"][0]["name"] == "default" and data["projects"][0]["files"] == [{"name": "notes.txt", "bytes": 11}]
        assert data["conversations"] == [{"thread": "chat1", "messages": [{"role": "user", "content": "hello there"}]}]
        assert data["push_browsers"] == 1
        everything = response.content + b"".join(archive.read(n) for n in names)
        for forbidden in (b"SECRET", b"password_hash", b"google_token", b"BOB", str(bob["_id"]).encode(), b"bob@example.com"):
            assert forbidden not in everything, forbidden

    try:
        run_with_db(scenario)
    finally:
        cleanup(folders)


def test_exports_are_limited_per_hour(monkeypatch):
    monkeypatch.setattr(get_settings(), "ACCOUNT_EXPORT_MAX_PER_HOUR", 1)

    async def scenario(db):
        user = await make_user(db, "hana@example.com")
        async with http(app_for(db, user)) as client:
            assert (await client.get("/api/v1/account/export")).status_code == 200
            assert (await client.get("/api/v1/account/export")).status_code == 429

    run_with_db(scenario)


def test_the_temporary_export_file_is_removed_after_the_download():
    before = set(Path(__import__("tempfile").gettempdir()).glob("nexusai-export-*.zip"))

    async def scenario(db):
        user = await make_user(db, "ivy@example.com")
        async with http(app_for(db, user)) as client:
            assert (await client.get("/api/v1/account/export")).status_code == 200

    run_with_db(scenario)
    assert set(Path(__import__("tempfile").gettempdir()).glob("nexusai-export-*.zip")) == before


# ── email verification ──────────────────────────────────────────────────────

def auth_app(db):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(local_auth.local_auth_router)
    app.include_router(notification_routes.notifications_router)
    app.state.services = SimpleNamespace(mongo_db=db, usage=None)
    return app


def test_signing_up_sends_a_verification_link_that_confirms_the_address_once(monkeypatch):
    sent = []
    monkeypatch.setattr(local_auth, "send_verification_email", lambda settings, email, raw: sent.append((email, raw)))

    async def scenario(db):
        async with http(auth_app(db)) as client:
            up = await client.post("/api/v1/auth/local/signup",
                                   json={"name": "Jo", "email": "Jo@Example.com", "password": SECRET_PASSWORD})
            assert up.status_code == 200 and up.json()["user"]["email_verified"] is False
            assert len(sent) == 1 and sent[0][0] == "jo@example.com"
            raw = sent[0][1]
            stored = (await db["users"].find_one({"email": "jo@example.com"}))
            assert stored["email_verified"] is False and raw not in json.dumps(stored, default=str)    # only a hash is kept

            assert (await client.post("/api/v1/auth/local/verify-email", json={"token": "wrong-token-value"})).status_code == 400
            assert (await client.post("/api/v1/auth/local/verify-email", json={"token": raw})).json() == {"verified": True}
            assert (await db["users"].find_one({"email": "jo@example.com"}))["email_verified"] is True
            again = await client.post("/api/v1/auth/local/verify-email", json={"token": raw})
            assert again.status_code == 400 and "not valid or has expired" in again.json()["detail"]      # single use

    run_with_db(scenario)


def test_an_expired_link_does_not_verify_and_a_new_one_replaces_the_old(monkeypatch):
    sent = []
    monkeypatch.setattr(local_auth, "send_verification_email", lambda settings, email, raw: sent.append(raw))

    async def scenario(db):
        users = UserModel(db)
        user = await make_user(db, "kim@example.com")
        await db["users"].update_one({"_id": user["_id"]}, {"$set": {"email_verified": False}})
        old = await users.start_email_verification(str(user["_id"]), 24)
        new = await users.start_email_verification(str(user["_id"]), 24)                       # asking again replaces it
        assert await users.confirm_email(old) is None and (await users.confirm_email(new))["email"] == "kim@example.com"

        expired = await users.start_email_verification(str(user["_id"]), 24)
        await db["users"].update_one({"_id": user["_id"]}, {"$set": {"email_verification.expires_at": datetime.utcnow() - timedelta(seconds=1)}})
        assert await users.confirm_email(expired) is None and await users.confirm_email("") is None

    run_with_db(scenario)


def test_resending_is_limited_and_already_verified_accounts_are_told_so(monkeypatch):
    sent = []
    monkeypatch.setattr(local_auth, "send_verification_email", lambda settings, email, raw: sent.append(raw))

    async def scenario(db):
        unverified = await make_user(db, "lee@example.com")
        await db["users"].update_one({"_id": unverified["_id"]}, {"$set": {"email_verified": False}})
        unverified["email_verified"] = False
        verified = await make_user(db, "max@example.com")
        app = auth_app(db)
        app.dependency_overrides[auth_utils.get_current_user] = lambda: unverified
        async with http(app) as client:
            assert [(await client.post("/api/v1/auth/local/resend-verification")).status_code for _ in range(3)] == [200] * 3
            assert (await client.post("/api/v1/auth/local/resend-verification")).status_code == 429
        assert len(sent) == 3
        app.dependency_overrides[auth_utils.get_current_user] = lambda: verified
        async with http(app) as client:
            assert (await client.post("/api/v1/auth/local/resend-verification")).json() == {"already_verified": True}

    run_with_db(scenario)


def test_google_sign_up_is_verified_from_the_start():
    async def scenario(db):
        user = await UserModel(db).create_google_user("Nina", "nina@example.com", "google-sub-1")
        assert user["email_verified"] is True

    run_with_db(scenario)


def test_the_link_goes_to_the_log_with_the_address_masked(caplog):
    settings = make(FRONTEND_URL="http://localhost:5173")
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        mailer.send_verification_email(settings, "someone@example.com", "TOKEN123")
    assert "http://localhost:5173/verify-email?token=TOKEN123" in caplog.text and "someone@example.com" not in caplog.text


def test_requiring_verification_blocks_unconfirmed_accounts_except_for_confirming_leaving_and_deleting(monkeypatch):
    monkeypatch.setattr(get_settings(), "REQUIRE_EMAIL_VERIFICATION", True)

    async def scenario(db):
        user = await make_user(db, "oli@example.com")
        await db["users"].update_one({"_id": user["_id"]}, {"$set": {"email_verified": False}})
        token = auth_utils.create_access_token({"sub": "oli@example.com"})
        headers = {"Authorization": f"Bearer {token}"}
        app = app_for(db, user, with_real_auth=True)
        app.include_router(local_auth.local_auth_router)
        async with http(app) as client:
            blocked = await client.get("/api/v1/notifications", headers=headers)
            assert blocked.status_code == 403 and "confirm your email" in blocked.json()["detail"]
            assert (await client.get("/api/v1/auth/local/me", headers=headers)).status_code == 200
            assert (await client.get("/api/v1/account/export", headers=headers)).status_code == 200
            await db["users"].update_one({"_id": user["_id"]}, {"$set": {"email_verified": True}})
            assert (await client.get("/api/v1/notifications", headers=headers)).status_code == 200

    run_with_db(scenario)


def test_verification_settings_are_checked():
    assert make().problems() == [] and make().EMAIL_DELIVERY == "log" and make().REQUIRE_EMAIL_VERIFICATION is False
    assert any("EMAIL_DELIVERY" in p for p in make(EMAIL_DELIVERY="smtp").problems())
    assert any("REQUIRE_EMAIL_VERIFICATION" in p for p in make(REQUIRE_EMAIL_VERIFICATION=True).problems())
