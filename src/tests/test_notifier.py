"""Persistent notifications and their channels (PR 0.2): one stored path for every producer, optional Web Push and
email-to-self per user, and the stream that reads from MongoDB. Real MongoDB (throwaway database); no network:
the two senders (push service, Gmail) are replaced by fakes. Skipped when MongoDB is unreachable."""
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import event_bus
from helpers.auth_utils import get_current_user
from helpers.config import Settings, get_settings
from helpers.error_handlers import install_error_handlers
from models.NotificationModel import NotificationModel
from models.PushSubscriptionModel import PushSubscriptionModel
from models.indexes import ensure_all_indexes
from routes import agent as agent_routes, notifications as notification_routes
from services import notifier
from services.notifier import PushGone, is_allowed_push_endpoint, notify

MONGO_URL = "mongodb://localhost:27017/"
FCM = "https://fcm.googleapis.com/fcm/send/abc123"


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
        name = f"test_notifier_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


@pytest.fixture
def push_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "public-key")
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "private-key")


async def make_user(db, address, google=False, **prefs):
    doc = {"email": address, "notification_prefs": prefs}
    if google:
        doc["google_token"] = {"token": "t", "refresh_token": "r"}
    return str((await db["users"].insert_one(doc)).inserted_id)


# ── what the server accepts as a push address ───────────────────────────────

@pytest.mark.parametrize("endpoint,ok", [
    (FCM, True),
    ("https://updates.push.services.mozilla.com/wpush/v2/xyz", True),
    ("https://web.push.apple.com/abc", True),
    ("https://par02p.notify.windows.com/w/?token=abc", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),                 # not https
    ("https://evil.example.com/fcm.googleapis.com", False),            # the host is what counts
    ("https://fcm.googleapis.com.evil.example.com/x", False),
    ("https://localhost/push", False),
    ("https://169.254.169.254/latest/meta-data", False),               # cloud metadata address
    ("https://fcm.googleapis.com:8443/x", False),                      # unusual port
    ("not a url", False),
    ("", False),
])
def test_only_known_push_services_are_accepted(endpoint, ok):
    assert is_allowed_push_endpoint(endpoint) is ok


def test_vapid_settings_are_checked():
    base = {"JWT_SECRET_KEY": "x" * 40}
    assert not [p for p in Settings(**base, VAPID_PUBLIC_KEY="a", VAPID_PRIVATE_KEY="b").problems() if "VAPID" in p]
    assert any("together" in p for p in Settings(**base, VAPID_PUBLIC_KEY="a").problems())
    assert any("VAPID_SUBJECT" in p for p in Settings(**base, VAPID_SUBJECT="admin@x.com").problems())


# ── the stored notification and the optional channels ───────────────────────

@needs_mongo
def test_every_notification_is_stored_whatever_the_channels_and_a_key_makes_it_once():
    async def scenario(db):
        alice = await make_user(db, "a@x.com")
        first = await notify(db, alice, "daily_summary", "☀️ Summary", "text", dedupe_key="k1")
        again = await notify(db, alice, "daily_summary", "☀️ Summary", "text", dedupe_key="k1")
        assert first is not None and again is None
        assert await db["notifications"].count_documents({}) == 1
        stored = await db["notifications"].find_one({})
        assert stored["read"] is False and "delivery" not in stored  # no optional channel was on

    run_with_db(scenario)


@needs_mongo
def test_push_goes_only_to_users_who_switched_it_on_and_only_to_their_own_browsers(push_on, monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "send_web_push", lambda sub, payload, settings: sent.append((sub["endpoint"], payload)))

    async def scenario(db):
        alice = await make_user(db, "a@x.com", push=True)
        bob = await make_user(db, "b@x.com")                     # Bob has a browser subscribed but push is off
        subs = PushSubscriptionModel(db)
        await subs.save(alice, FCM + "-alice", "p", "a")
        await subs.save(bob, FCM + "-bob", "p", "a")

        stored = await notify(db, alice, "reminder", "🔔 Reminder: call", "bring files")
        await notify(db, bob, "reminder", "🔔 Reminder: bob's", "")

        assert [endpoint for endpoint, _ in sent] == [FCM + "-alice"]
        assert sent[0][1]["title"] == "🔔 Reminder: call" and sent[0][1]["id"] == str(stored["_id"])
        record = await db["notifications"].find_one({"_id": stored["_id"]})
        assert record["delivery"]["push"]["status"] == "sent"
        assert "delivery" not in await db["notifications"].find_one({"user_id": bob})

    run_with_db(scenario)


@needs_mongo
def test_an_expired_push_subscription_is_removed_and_a_failing_one_is_recorded_not_fatal(push_on, monkeypatch):
    def fake_send(sub, payload, settings):
        if sub["endpoint"].endswith("gone"):
            raise PushGone()
        raise RuntimeError("push service down")

    monkeypatch.setattr(notifier, "send_web_push", fake_send)

    async def scenario(db):
        alice = await make_user(db, "a@x.com", push=True)
        await PushSubscriptionModel(db).save(alice, FCM + "-gone", "p", "a")
        await notify(db, alice, "test", "t")
        assert await PushSubscriptionModel(db).list_for_user(alice) == []

        await PushSubscriptionModel(db).save(alice, FCM + "-broken", "p", "a")
        stored = await notify(db, alice, "test", "t2")
        record = await db["notifications"].find_one({"_id": stored["_id"]})
        assert record["delivery"]["push"]["status"] == "failed"      # recorded ...
        assert record["read"] is False                                # ... and the notification is still there

    run_with_db(scenario)


@needs_mongo
def test_push_without_server_keys_or_browsers_is_skipped_with_a_reason(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", None)

    async def scenario(db):
        alice = await make_user(db, "a@x.com", push=True)
        stored = await notify(db, alice, "test", "t")
        record = await db["notifications"].find_one({"_id": stored["_id"]})
        assert record["delivery"]["push"]["status"] == "skipped" and "not configured" in record["delivery"]["push"]["reason"]

    run_with_db(scenario)


@needs_mongo
def test_email_goes_from_the_users_own_gmail_to_themself_only_when_switched_on(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier, "send_gmail_to_self",
                        lambda token, to, subject, body: calls.append((token["token"], to, subject, body)))

    async def scenario(db):
        alice = await make_user(db, "alice@x.com", google=True, email=True)
        bob = await make_user(db, "bob@x.com", google=True)              # email off
        carol = await make_user(db, "carol@x.com", email=True)           # on, but no Google connected

        await notify(db, alice, "reminder", "🔔 Reminder: call", "bring files")
        await notify(db, bob, "reminder", "🔔 Reminder: bob's")
        stored = await notify(db, carol, "reminder", "🔔 Reminder: carol's")

        assert calls == [("t", "alice@x.com", "🔔 Reminder: call", "bring files")]   # to herself, nobody else
        record = await db["notifications"].find_one({"_id": stored["_id"]})
        assert record["delivery"]["email"]["status"] == "skipped"

    run_with_db(scenario)


@needs_mongo
def test_a_failing_email_does_not_lose_the_notification(monkeypatch):
    def broken(token, to, subject, body):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(notifier, "send_gmail_to_self", broken)

    async def scenario(db):
        alice = await make_user(db, "alice@x.com", google=True, email=True)
        stored = await notify(db, alice, "reminder", "🔔 Reminder: call")
        record = await db["notifications"].find_one({"_id": stored["_id"]})
        assert record["delivery"]["email"]["status"] == "failed" and record["read"] is False

    run_with_db(scenario)


# ── the producers use the same path ─────────────────────────────────────────

@needs_mongo
def test_an_urgent_inbox_alert_is_stored_once_even_after_the_in_memory_memory_is_lost(monkeypatch):
    from controllers import proactive_agent
    from controllers.llm_schemas import UrgentVerdict

    monkeypatch.setattr(proactive_agent, "_fetch_unread",
                        lambda token: ([{"message_id": "m1", "subject": "s", "snippet": "x", "sender": "a"}], None))

    async def fake_llm(*args, **kwargs):
        return UrgentVerdict(is_urgent=True, message="Call the client back today")

    monkeypatch.setattr(proactive_agent, "llm_json", fake_llm)

    async def scenario(db):
        alice = await make_user(db, "alice@x.com")
        services = SimpleNamespace(mongo_db=db, generation_client=object())
        user = {"user_id": alice, "email": "alice@x.com", "google_token": {"t": 1}}
        await proactive_agent._evaluate_user(services, user)
        proactive_agent._seen_message_ids.clear()          # what a restart does
        await proactive_agent._evaluate_user(services, user)
        notes = await NotificationModel(db).list_for_user(alice)
        assert len(notes) == 1 and notes[0]["kind"] == "inbox_alert" and "Call the client back" in notes[0]["body"]

    run_with_db(scenario)


@needs_mongo
def test_the_daily_summary_is_stored_once_per_local_day(monkeypatch):
    from services import notification_engine

    class FakeSummary:
        def __init__(self, **kwargs): ...

        async def generate(self, project_id, user_id):
            return {"summary": "Two meetings today."}

    monkeypatch.setattr(notification_engine, "DailySummaryService", FakeSummary)
    monkeypatch.setattr(notification_engine, "_build_clients", lambda token: (None, None, None))

    async def scenario(db):
        alice = await make_user(db, "alice@x.com", google=True)
        engine = notification_engine.NotificationEngine(SimpleNamespace(mongo_db=db, generation_client=None))
        user = {"user_id": alice, "email": "alice@x.com", "google_token": {"t": 1}, "timezone": "Africa/Cairo"}
        await engine._generate_for_user(user)
        await engine._generate_for_user(user)              # a restart repeats the work; the key stops a second copy
        notes = await NotificationModel(db).list_for_user(alice)
        assert len(notes) == 1 and notes[0]["kind"] == "daily_summary" and notes[0]["body"] == "Two meetings today."

    run_with_db(scenario)


# ── the API ─────────────────────────────────────────────────────────────────

def make_app(db, user):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(notification_routes.notifications_router)
    app.state.services = SimpleNamespace(mongo_db=db)
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@needs_mongo
def test_channel_preferences_are_per_user_and_refuse_what_cannot_be_delivered(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", None)
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", None)

    async def scenario(db):
        alice_id = await make_user(db, "alice@x.com", google=True)
        bob_id = await make_user(db, "bob@x.com")
        alice = await db["users"].find_one({"_id": ObjectId(alice_id)})
        bob = await db["users"].find_one({"_id": ObjectId(bob_id)})

        async with http(make_app(db, alice)) as client:
            prefs = (await client.get("/api/v1/notifications/preferences")).json()
            assert prefs["push"] is False and prefs["email"] is False
            assert prefs["push_available"] is False and prefs["email_available"] is True
            assert (await client.put("/api/v1/notifications/preferences", json={"push": True})).status_code == 409
            ok = await client.put("/api/v1/notifications/preferences", json={"email": True})
            assert ok.status_code == 200 and ok.json() == {"push": False, "email": True}

        async with http(make_app(db, bob)) as client:       # Bob is unaffected, and has no Google to send from
            assert (await client.get("/api/v1/notifications/preferences")).json()["email"] is False
            assert (await client.put("/api/v1/notifications/preferences", json={"email": True})).status_code == 409

    run_with_db(scenario)


@needs_mongo
def test_push_subscriptions_are_validated_owned_and_moved_when_another_user_signs_in(push_on):
    async def scenario(db):
        alice_id = await make_user(db, "alice@x.com")
        bob_id = await make_user(db, "bob@x.com")
        alice = await db["users"].find_one({"_id": ObjectId(alice_id)})
        bob = await db["users"].find_one({"_id": ObjectId(bob_id)})
        body = {"endpoint": FCM, "keys": {"p256dh": "key", "auth": "secret"}}

        async with http(make_app(db, alice)) as client:
            assert (await client.get("/api/v1/notifications/push/public-key")).json() == {"public_key": "public-key"}
            evil = {"endpoint": "https://169.254.169.254/x", "keys": {"p256dh": "k", "auth": "a"}}
            assert (await client.post("/api/v1/notifications/push/subscription", json=evil)).status_code == 400
            assert (await client.post("/api/v1/notifications/push/subscription", json=body)).status_code == 200
            assert len(await PushSubscriptionModel(db).list_for_user(alice_id)) == 1
            assert (await client.get("/api/v1/notifications/preferences")).json()["push_browsers"] == 1

        async with http(make_app(db, bob)) as client:       # same browser, Bob signs in
            assert (await client.post("/api/v1/notifications/push/subscription", json=body)).status_code == 200
            assert await PushSubscriptionModel(db).list_for_user(alice_id) == []
            assert len(await PushSubscriptionModel(db).list_for_user(bob_id)) == 1
            # Alice cannot remove Bob's subscription.
        async with http(make_app(db, alice)) as client:
            assert (await client.post("/api/v1/notifications/push/unsubscribe", json={"endpoint": FCM})).json() == {"removed": False}
        async with http(make_app(db, bob)) as client:
            assert (await client.post("/api/v1/notifications/push/unsubscribe", json={"endpoint": FCM})).json() == {"removed": True}

    run_with_db(scenario)


@needs_mongo
def test_the_test_notification_reaches_the_stored_list():
    async def scenario(db):
        alice_id = await make_user(db, "alice@x.com")
        alice = await db["users"].find_one({"_id": ObjectId(alice_id)})
        async with http(make_app(db, alice)) as client:
            assert (await client.post("/api/v1/notifications/test")).json()["sent"] is True
            titles = [n["title"] for n in (await client.get("/api/v1/notifications")).json()["notifications"]]
            assert titles == ["✅ Test notification"]

    run_with_db(scenario)


# ── the stream ──────────────────────────────────────────────────────────────

class FakeRequest:
    """Just enough of a Request for the stream: the app state and a connection that drops on demand."""

    def __init__(self, db):
        self.app = SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(mongo_db=db)))
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


async def read_events(response, request, want, timeout=8):
    """Collect notification events from the stream until `want` arrived (then hang up) or the time is up."""
    events = []

    async def consume():
        async for chunk in response.body_iterator:
            if chunk.startswith("event: notification"):
                events.append(chunk)
                if len(events) >= want:
                    break

    try:
        await asyncio.wait_for(consume(), timeout)
    except asyncio.TimeoutError:
        pass
    request.disconnected = True
    return events


@needs_mongo
def test_the_stream_replays_what_was_missed_offline_to_the_right_user_only():
    """Nothing depends on a connected browser or on process memory: stored notifications wait, and a fresh stream
    (a new process after a restart looks the same) delivers them."""
    async def scenario(db):
        alice, bob = await make_user(db, "a@x.com"), await make_user(db, "b@x.com")
        await notify(db, alice, "reminder", "🔔 Reminder: first")
        await notify(db, alice, "daily_summary", "☀️ Second")
        await notify(db, bob, "reminder", "🔔 Reminder: bob's")
        read, _ = await NotificationModel(db).create(alice, "reminder", "🔔 Reminder: already read")
        await NotificationModel(db).mark_read(alice, str(read["_id"]))

        request = FakeRequest(db)
        response = await agent_routes.notifications_endpoint(request, event_bus.issue_ticket(alice))
        events = await read_events(response, request, want=2)
        assert len(events) == 2
        assert "first" in events[0] and "Second" in events[1]         # oldest first
        assert not any("bob" in e or "already read" in e for e in events)

    run_with_db(scenario)


@needs_mongo
def test_the_stream_delivers_a_notification_created_while_it_is_open():
    async def scenario(db):
        alice = await make_user(db, "a@x.com")
        request = FakeRequest(db)
        response = await agent_routes.notifications_endpoint(request, event_bus.issue_ticket(alice))
        reader = asyncio.create_task(read_events(response, request, want=1, timeout=10))
        await asyncio.sleep(0.5)
        await notify(db, alice, "reminder", "🔔 Reminder: live")
        events = await reader
        assert len(events) == 1 and "live" in events[0]

    run_with_db(scenario)


@needs_mongo
def test_the_stream_refuses_a_bad_or_used_ticket():
    async def scenario(db):
        request = FakeRequest(db)
        assert (await agent_routes.notifications_endpoint(request, "made-up")).status_code == 401
        ticket = event_bus.issue_ticket("alice")
        stream = await agent_routes.notifications_endpoint(request, ticket)
        request.disconnected = True
        assert [c async for c in stream.body_iterator] == []
        assert (await agent_routes.notifications_endpoint(request, ticket)).status_code == 401   # single use

    run_with_db(scenario)
