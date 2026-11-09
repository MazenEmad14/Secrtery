"""Reminders that fire (PR 0.1): due moments, the scan that delivers them exactly once, the job that runs the scan,
and the notifications API. Runs against a throwaway MongoDB database; skipped when none is reachable."""
import asyncio
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.deps import AgentDeps
from agent.nodes.actions import save_reminder_node
from helpers.auth_utils import get_current_user
from helpers.error_handlers import install_error_handlers
from models.JobModel import JobModel
from models.NotificationModel import NotificationModel
from models.indexes import ensure_all_indexes
from routes import notifications as notification_routes
from services.jobs import HANDLERS, SCAN_DUE_REMINDERS, JobContext, JobWorker
from services.reminders import backfill_due_dates, compute_due_at, deliver_due_reminders, scan_due_reminders

MONGO_URL = "mongodb://localhost:27017/"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


def run_with_db(coro_fn):
    async def runner():
        conn = AsyncIOMotorClient(MONGO_URL)
        name = f"test_reminders_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


def reminder_doc(user_id="alice", task="call the dentist", due_in=-5, **extra):
    """A reminder as ReminderModel really stores it: only the fields that were set, so no `is_completed`,
    `created_at` or `delivered_at` (a first version of the scan missed every real reminder because of that).
    `due_in` is seconds from now (negative = already due)."""
    doc = {"task": task, "date": "", "time": "", "priority": "medium", "notes": "",
           "user_id": user_id, "project_id": "default",
           "due_at": datetime.utcnow() + timedelta(seconds=due_in)}
    doc.update(extra)
    return doc


# ── when is a reminder due ──────────────────────────────────────────────────

def test_date_and_time_are_read_in_the_users_zone():
    # Cairo is UTC+2 in winter, +3 in summer (DST): 14:00 local is 12:00 UTC on 2026-01-15.
    assert compute_due_at("2026-01-15", "14:00", "Africa/Cairo") == datetime(2026, 1, 15, 12, 0)
    assert compute_due_at("2026-01-15", "14:00", "UTC") == datetime(2026, 1, 15, 14, 0)
    assert compute_due_at("2026-07-15", "14:00", "Asia/Riyadh") == datetime(2026, 7, 15, 11, 0)


def test_a_date_without_a_time_is_due_at_the_summary_hour_local():
    assert compute_due_at("2026-01-15", "", "UTC", default_hour=8) == datetime(2026, 1, 15, 8, 0)


def test_a_time_without_a_date_means_the_day_it_was_created_in_the_users_zone():
    created = datetime(2026, 1, 15, 22, 30)  # 00:30 on the 16th in Cairo
    assert compute_due_at("", "09:00", "Africa/Cairo", created_at=created) == datetime(2026, 1, 16, 7, 0)


@pytest.mark.parametrize("date,time_", [("", ""), ("tomorrow", "10:00"), ("2026-13-45", ""), ("2026-01-15", "25:00"),
                                        ("2026-01-15", "soon")])
def test_unusable_dates_and_times_give_no_due_moment(date, time_):
    assert compute_due_at(date, time_, "UTC") is None


# ── the scan ────────────────────────────────────────────────────────────────

@needs_mongo
def test_a_due_reminder_becomes_one_notification_and_is_marked_delivered():
    async def scenario(db):
        due = (await db["reminders"].insert_one(reminder_doc(task="call the dentist", notes="bring x-rays"))).inserted_id
        future = (await db["reminders"].insert_one(reminder_doc(task="later", due_in=3600))).inserted_id
        done = (await db["reminders"].insert_one(reminder_doc(task="done", is_completed=True))).inserted_id

        assert await deliver_due_reminders(db) == 1

        notes = await NotificationModel(db).list_for_user("alice")
        assert len(notes) == 1
        assert "call the dentist" in notes[0]["title"] and notes[0]["body"] == "bring x-rays"
        assert notes[0]["kind"] == "reminder" and notes[0]["read"] is False
        assert notes[0]["data"]["reminder_id"] == str(due)
        assert (await db["reminders"].find_one({"_id": due}))["delivered_at"] is not None
        # Not delivered = no `delivered_at` (reminders are stored without unset fields).
        assert (await db["reminders"].find_one({"_id": future})).get("delivered_at") is None
        assert (await db["reminders"].find_one({"_id": done})).get("delivered_at") is None

    run_with_db(scenario)


@needs_mongo
def test_a_reminder_saved_through_the_reminder_model_is_delivered():
    """The path the app takes: ReminderModel.create_reminder, then the scan."""
    async def scenario(db):
        from models.ReminderModel import ReminderModel
        from models.db_schemes import Reminder

        past = datetime.utcnow() - timedelta(minutes=1)
        await ReminderModel(db).create_reminder(Reminder(task="from the model", user_id="alice", due_at=past))
        assert await deliver_due_reminders(db) == 1

    run_with_db(scenario)


@needs_mongo
def test_scanning_again_does_not_deliver_again():
    async def scenario(db):
        await db["reminders"].insert_one(reminder_doc())
        assert await deliver_due_reminders(db) == 1
        assert await deliver_due_reminders(db) == 0
        assert await db["notifications"].count_documents({}) == 1

    run_with_db(scenario)


@needs_mongo
def test_a_crash_between_storing_the_notification_and_marking_the_reminder_does_not_duplicate():
    async def scenario(db):
        rid = (await db["reminders"].insert_one(reminder_doc())).inserted_id
        # The state a crash leaves behind: the notification exists, the reminder is not marked.
        await NotificationModel(db).create("alice", "reminder", "🔔 Reminder: call the dentist",
                                           dedupe_key=f"reminder:{rid}")
        assert await deliver_due_reminders(db) == 0  # nothing new was created ...
        assert (await db["reminders"].find_one({"_id": rid}))["delivered_at"] is not None  # ... but it is marked now
        assert await db["notifications"].count_documents({}) == 1

    run_with_db(scenario)


@needs_mongo
def test_200_reminders_are_each_delivered_exactly_once_even_with_overlapping_scans():
    async def scenario(db):
        ids = []
        for i in range(200):
            user = f"user{i % 7}"
            ids.append((await db["reminders"].insert_one(reminder_doc(user_id=user, task=f"task {i}", due_in=-i))).inserted_id)

        # Four workers scanning at the same moment, twice over.
        for _ in range(2):
            await asyncio.gather(*(deliver_due_reminders(db) for _ in range(4)))

        assert await db["notifications"].count_documents({}) == 200
        per_reminder = {}
        async for note in db["notifications"].find({}):
            per_reminder[note["data"]["reminder_id"]] = per_reminder.get(note["data"]["reminder_id"], 0) + 1
        assert set(per_reminder) == {str(i) for i in ids} and set(per_reminder.values()) == {1}
        assert await db["reminders"].count_documents({"delivered_at": None}) == 0

    run_with_db(scenario)


@needs_mongo
def test_reminders_without_a_due_moment_are_scheduled_from_their_date_and_the_users_zone():
    async def scenario(db):
        user_id = (await db["users"].insert_one({"email": "a@example.com", "timezone": "Africa/Cairo"})).inserted_id
        legacy = reminder_doc(user_id=str(user_id), date="2026-01-15", time="14:00")
        legacy.pop("due_at")
        unusable = reminder_doc(user_id=str(user_id), date="", time="")
        unusable.pop("due_at")
        legacy_id = (await db["reminders"].insert_one(legacy)).inserted_id
        unusable_id = (await db["reminders"].insert_one(unusable)).inserted_id

        assert await backfill_due_dates(db) == 1
        assert (await db["reminders"].find_one({"_id": legacy_id}))["due_at"] == datetime(2026, 1, 15, 12, 0)
        flagged = await db["reminders"].find_one({"_id": unusable_id})
        assert flagged["due_unparsed"] is True and flagged.get("due_at") is None
        assert await backfill_due_dates(db) == 0  # the unusable one is not looked at again

        # and the scan delivers the scheduled one (it is in the past), never the unusable one
        assert (await scan_due_reminders(db))["delivered"] == 1

    run_with_db(scenario)


@needs_mongo
def test_approving_a_reminder_schedules_it():
    async def scenario(db):
        state = {"query": "remind me", "user_id": "alice", "user_timezone": "Africa/Cairo", "project_id": "default",
                 "priority": "medium", "metadata": {},
                 "action_details": {"task": "send the offer", "date": "2026-01-15", "time": "14:00"}}
        await save_reminder_node(state, AgentDeps(db_client=db))
        saved = await db["reminders"].find_one({"user_id": "alice"})
        assert saved["due_at"] == datetime(2026, 1, 15, 12, 0)

    run_with_db(scenario)


# ── the job on the queue ────────────────────────────────────────────────────

@needs_mongo
def test_a_reminder_due_in_a_moment_is_delivered_by_the_worker_within_the_scan_interval():
    """The real path: a job on the queue, run by a worker, as the scheduler would enqueue it. The reminder is due in
    2 seconds; the scan is queued every 0.5 seconds (the app does it every 20), so it must appear shortly after."""
    async def scenario(db):
        settings = SimpleNamespace(JOB_CONCURRENCY=2, JOB_POLL_SECONDS=0.1, JOB_LEASE_SECONDS=30,
                                   JOB_RETENTION_DAYS=7, JOB_MAX_ATTEMPTS=3)
        worker = JobWorker(JobContext(db, None, None, None, None), db, settings, handlers=HANDLERS, worker_id="t")
        task = asyncio.create_task(worker.run())
        await db["reminders"].insert_one(reminder_doc(due_in=2))
        due_moment = time.monotonic() + 2
        delivered_at = None
        try:
            while time.monotonic() < due_moment + 10:
                await JobModel(db).enqueue(SCAN_DUE_REMINDERS, "system", "all", "all", {}, 3)
                if await db["notifications"].count_documents({}):
                    delivered_at = time.monotonic()
                    break
                await asyncio.sleep(0.5)
        finally:
            await worker.stop()
            await task
        assert delivered_at is not None, "the reminder was never delivered"
        assert delivered_at >= due_moment - 0.5, "delivered before it was due"
        assert delivered_at - due_moment < 3, f"delivered {delivered_at - due_moment:.1f}s after it was due"

    run_with_db(scenario)


@needs_mongo
def test_only_one_scan_job_is_queued_at_a_time():
    async def scenario(db):
        jobs = JobModel(db)
        _, first = await jobs.enqueue(SCAN_DUE_REMINDERS, "system", "all", "all", {}, 3)
        _, second = await jobs.enqueue(SCAN_DUE_REMINDERS, "system", "all", "all", {}, 3)
        assert (first, second) == (True, False)

    run_with_db(scenario)


# ── the API ─────────────────────────────────────────────────────────────────

@needs_mongo
def test_notifications_are_listed_and_marked_read_per_user():
    async def scenario(db):
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(notification_routes.notifications_router)
        app.state.services = SimpleNamespace(mongo_db=db)
        current = {"user": {"_id": ObjectId()}}
        app.dependency_overrides[get_current_user] = lambda: current["user"]
        alice, bob = current["user"], {"_id": ObjectId()}

        model = NotificationModel(db)
        mine, _ = await model.create(str(alice["_id"]), "reminder", "🔔 Reminder: mine")
        theirs, _ = await model.create(str(bob["_id"]), "reminder", "🔔 Reminder: theirs")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listed = (await client.get("/api/v1/notifications")).json()["notifications"]
            assert [n["title"] for n in listed] == ["🔔 Reminder: mine"]

            # Alice cannot touch Bob's notification: it looks like it does not exist.
            assert (await client.post(f"/api/v1/notifications/{theirs['_id']}/read")).status_code == 404
            assert (await client.post("/api/v1/notifications/not-an-id/read")).status_code == 404
            assert (await db["notifications"].find_one({"_id": theirs["_id"]}))["read"] is False

            assert (await client.post(f"/api/v1/notifications/{mine['_id']}/read")).status_code == 200
            assert (await client.get("/api/v1/notifications?unread_only=true")).json()["notifications"] == []
            assert len((await client.get("/api/v1/notifications")).json()["notifications"]) == 1

    run_with_db(scenario)


@needs_mongo
def test_unread_notifications_wait_for_a_user_who_was_offline():
    """Nothing about delivery depends on a connected browser: the notification is just stored until it is read."""
    async def scenario(db):
        await db["reminders"].insert_one(reminder_doc(user_id="alice"))
        await scan_due_reminders(db)
        # ... server restarts, user comes back later: a fresh model sees it
        unread = await NotificationModel(db).list_for_user("alice", unread_only=True)
        assert len(unread) == 1
        assert await NotificationModel(db).list_for_user("bob", unread_only=True) == []

    run_with_db(scenario)
