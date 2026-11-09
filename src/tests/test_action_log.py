"""The audit log and idempotent actions (PR 0.4): every approval-gated action leaves a trail, runs at most once, and a
retry returns the stored result. The fault-injection tests use the real MongoDB model (a throwaway database) and are
skipped when none is reachable; the rest need no database."""
import asyncio
import random
import sys
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

from action_fakes import FakeActionLog, approve
from agent import AgentDeps
from agent.nodes import actions as action_nodes
from helpers.auth_utils import get_current_user
from helpers.error_handlers import install_error_handlers
from helpers.exceptions import ExternalServiceError, GoogleAuthRequiredException, ValidationFailed
from models.ActionLogModel import ActionLogModel
from models.indexes import ensure_all_indexes
from routes import actions as action_routes
from services import actions as gate
from services.actions import ActionNotApproved, google_event_id, params_hash, run_once

MONGO_URL = "mongodb://localhost:27017/"
EMAIL = {"recipient": "ahmed@example.com", "subject": "Running late", "body": "Hi Ahmed, SECRET BODY TEXT."}


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
        name = f"test_actions_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


class Counter:
    """Stands in for the outside service: counts how many times it was really called."""

    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = 0, result or {"message_id": "gmail-1", "to": "a@x.com"}, error

    async def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.result)


async def approved_action(log, user="u1", node="send_email", params=EMAIL):
    proposal = await gate.propose(log, user, node, params)
    await gate.record_decision(log, user, proposal["action_id"], node, approved=True)
    return proposal["action_id"]


# ── what is hashed and shown ────────────────────────────────────────────────

def test_the_parameter_hash_ignores_order_but_notices_any_change():
    a = params_hash("send_email", {"recipient": "a@x.com", "subject": "s", "body": "b"})
    assert a == params_hash("send_email", {"body": "b", "subject": "s", "recipient": "a@x.com"})
    assert a != params_hash("send_email", {"recipient": "evil@x.com", "subject": "s", "body": "b"})
    assert a != params_hash("create_calendar_event", {"recipient": "a@x.com", "subject": "s", "body": "b"})
    assert params_hash("send_email", {"body": "ب" * 5}) == params_hash("send_email", {"body": "ب" * 5})   # Arabic is fine


def test_the_log_shows_who_and_what_never_the_message_body():
    summary = gate.summarize("send_email", EMAIL)
    assert summary == {"to": "ahmed@example.com", "subject": "Running late"} and "SECRET" not in str(summary)
    assert "SECRET" not in str(gate.summarize("drive_save", {"title": "Notes", "content": "SECRET BODY TEXT"}))


def test_calendar_event_ids_are_valid_for_google_and_stable():
    first = google_event_id("abc")
    assert first == google_event_id("abc") and first != google_event_id("abd")
    assert 5 <= len(first) <= 1024 and set(first) <= set("0123456789abcdefghijklmnopqrstuv")


# ── the gate ────────────────────────────────────────────────────────────────

def test_nothing_runs_without_an_approval_record_or_if_it_was_rejected():
    async def scenario():
        log, outside = FakeActionLog(), Counter()
        proposal = await gate.propose(log, "u1", "send_email", EMAIL)       # proposed, never answered
        with pytest.raises(ActionNotApproved):
            await run_once(log, "u1", proposal["action_id"], "send_email", EMAIL, outside)
        await gate.record_decision(log, "u1", proposal["action_id"], "send_email", approved=False)
        with pytest.raises(ActionNotApproved):
            await run_once(log, "u1", proposal["action_id"], "send_email", EMAIL, outside)
        with pytest.raises(ActionNotApproved):                                # an id nobody proposed
            await run_once(log, "u1", "made-up", "send_email", EMAIL, outside)
        assert outside.calls == 0 and log.kinds(proposal["action_id"]) == ["proposed", "rejected"]

    asyncio.run(scenario())


def test_another_users_approval_does_not_count():
    async def scenario():
        log, outside = FakeActionLog(), Counter()
        action_id = await approved_action(log, user="alice")
        with pytest.raises(ActionNotApproved):
            await run_once(log, "bob", action_id, "send_email", EMAIL, outside)
        assert outside.calls == 0

    asyncio.run(scenario())


def test_what_runs_must_be_what_was_approved():
    async def scenario():
        log, outside = FakeActionLog(), Counter()
        action_id = await approved_action(log)
        tampered = {**EMAIL, "recipient": "attacker@example.com"}            # changed after the user approved
        with pytest.raises(ActionNotApproved):
            await run_once(log, "u1", action_id, "send_email", tampered, outside)
        assert outside.calls == 0 and log.kinds(action_id)[-1] == "failed"
        revived = await run_once(log, "u1", action_id, "send_email", EMAIL, outside)   # the right parameters cannot revive it
        assert revived.status == "failed" and revived.replayed and "changed after you approved" in revived.error
        assert outside.calls == 0

    asyncio.run(scenario())


def test_a_successful_action_leaves_the_full_trail_and_a_retry_returns_the_stored_result():
    async def scenario():
        log, outside = FakeActionLog(), Counter()
        action_id = await approved_action(log)
        first = await run_once(log, "u1", action_id, "send_email", EMAIL, outside, result_id_key="message_id")
        assert first.status == "executed" and first.replayed is False and outside.calls == 1
        assert log.kinds(action_id) == ["proposed", "approved", "executing", "executed"]
        executed = next(e for e in log.entries if e["event"] == "executed")
        assert executed["result_id"] == "gmail-1" and executed["executed_at"] is not None
        approved = next(e for e in log.entries if e["event"] == "approved")
        assert approved["approver"] == "u1" and approved["approved_at"] is not None

        for _ in range(3):                                                    # retries, even from other requests
            again = await run_once(log, "u1", action_id, "send_email", EMAIL, outside, result_id_key="message_id")
            assert again.status == "executed" and again.replayed is True and again.result == first.result
        assert outside.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [
    ValidationFailed("That email address is not valid."),
    GoogleAuthRequiredException(),
    ExternalServiceError("Gmail 403", service="Gmail", http_status=403),
])
def test_a_refusal_before_anything_happened_is_recorded_as_failed_and_not_retried(error):
    async def scenario():
        log, outside = FakeActionLog(), Counter(error=error)
        action_id = await approved_action(log)
        first = await run_once(log, "u1", action_id, "send_email", EMAIL, outside)
        assert first.status == "failed" and first.exception is error
        again = await run_once(log, "u1", action_id, "send_email", EMAIL, outside)
        assert again.status == "failed" and again.replayed and again.error == first.error
        assert outside.calls == 1 and log.kinds(action_id) == ["proposed", "approved", "executing", "failed"]

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [
    ExternalServiceError("Gmail 500", service="Gmail", http_status=500),
    ExternalServiceError("timeout", service="Gmail"),
    TimeoutError("read timed out"),
])
def test_an_unclear_failure_is_never_retried_for_an_email(error):
    """A timeout or a 5xx can happen after Gmail accepted the message. Sending again could deliver it twice."""
    async def scenario():
        log, outside = FakeActionLog(), Counter(error=error)
        action_id = await approved_action(log)
        first = await run_once(log, "u1", action_id, "send_email", EMAIL, outside)
        assert first.status == "unconfirmed"
        for _ in range(3):
            assert (await run_once(log, "u1", action_id, "send_email", EMAIL, outside)).status == "unconfirmed"
        assert outside.calls == 1 and log.kinds(action_id)[-1] == "unknown"

    asyncio.run(scenario())


def test_a_crash_in_the_middle_is_unconfirmed_after_the_lease_and_never_repeated():
    class Died(BaseException):
        """The process dies: not an Exception, so nothing in the gate gets to record an outcome."""

    async def scenario():
        log, outside = FakeActionLog(), Counter(error=Died())
        action_id = await approved_action(log)
        with pytest.raises(Died):
            await run_once(log, "u1", action_id, "send_email", EMAIL, outside)
        # right after: another attempt may still be running
        assert (await run_once(log, "u1", action_id, "send_email", EMAIL, outside)).status == "in_progress"
        later = datetime.utcnow() + timedelta(seconds=gate.EXECUTING_LEASE_SECONDS + 5)
        assert (await run_once(log, "u1", action_id, "send_email", EMAIL, outside, now=later)).status == "unconfirmed"
        assert (await run_once(log, "u1", action_id, "send_email", EMAIL, outside)).status == "unconfirmed"
        assert outside.calls == 1

    asyncio.run(scenario())


def test_an_action_that_is_safe_to_repeat_is_run_again_after_an_unclear_failure():
    async def scenario():
        log, outside = FakeActionLog(), Counter(error=TimeoutError("slow"))
        action_id = await approved_action(log, node="create_calendar_event", params={"title": "x"})
        first = await run_once(log, "u1", action_id, "create_calendar_event", {"title": "x"}, outside, idempotent=True)
        assert first.status == "failed" and log.kinds(action_id)[-1] == "executing"      # nothing final recorded
        outside.error = None
        second = await run_once(log, "u1", action_id, "create_calendar_event", {"title": "x"}, outside, idempotent=True)
        assert second.status == "executed" and outside.calls == 2

    asyncio.run(scenario())


def test_two_requests_for_the_same_action_at_once_run_it_once():
    async def scenario():
        log = FakeActionLog()
        action_id = await approved_action(log)
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(0.05)
            return {"message_id": "m"}

        outside = Counter()
        results = await asyncio.gather(
            run_once(log, "u1", action_id, "send_email", EMAIL, slow, result_id_key="message_id"),
            run_once(log, "u1", action_id, "send_email", EMAIL, outside, result_id_key="message_id"),
        )
        assert sorted(r.status for r in results) == ["executed", "in_progress"] and outside.calls == 0

    asyncio.run(scenario())


# ── the log itself ──────────────────────────────────────────────────────────

def test_the_log_model_can_only_add_and_read():
    public = {name for name in dir(ActionLogModel) if not name.startswith("_")}
    assert {"append", "events", "recent_events"} <= public
    assert not any(word in name for name in public for word in ("update", "delete", "remove", "replace", "drop", "set"))


@needs_mongo
def test_an_entry_kind_can_only_be_written_once_per_action():
    async def scenario(db):
        log = ActionLogModel(db)
        assert await log.append({"user_id": "u", "action_id": "a1", "event": "executing"}) is True
        assert await log.append({"user_id": "u", "action_id": "a1", "event": "executing"}) is False
        assert await db["actions_log"].count_documents({"action_id": "a1"}) == 1
        with pytest.raises(ValueError):
            await log.append({"user_id": "u", "action_id": "a1", "event": "deleted"})

    run_with_db(scenario)


@needs_mongo
def test_the_log_is_per_user():
    async def scenario(db):
        log = ActionLogModel(db)
        action_id = await approved_action(log, user="alice")
        assert len(await log.events("alice", action_id)) == 2 and await log.events("bob", action_id) == []

    run_with_db(scenario)


def test_history_groups_entries_into_one_row_per_action_with_the_right_status():
    async def scenario():
        log = FakeActionLog()
        done = await approved_action(log)
        await run_once(log, "u1", done, "send_email", EMAIL, Counter(), result_id_key="message_id")
        rejected = (await gate.propose(log, "u1", "save_reminder", {"task": "t"}))["action_id"]
        await gate.record_decision(log, "u1", rejected, "save_reminder", approved=False)
        waiting = (await gate.propose(log, "u1", "drive_save", {"title": "Notes"}))["action_id"]
        failed = await approved_action(log)
        await run_once(log, "u1", failed, "send_email", EMAIL, Counter(error=ValidationFailed("bad")))

        rows = {r["action_id"]: r for r in gate.history(list(reversed(log.entries)))}
        assert rows[done]["status"] == "done" and rows[done]["result_id"] == "gmail-1" and rows[done]["decided_at"]
        assert rows[done]["summary"] == {"to": "ahmed@example.com", "subject": "Running late"}
        assert rows[rejected]["status"] == "rejected"
        assert rows[waiting]["status"] == "awaiting_approval" and rows[failed]["status"] == "failed"
        assert rows[failed]["error"] == "bad"
        later = datetime.utcnow() + gate.PROPOSAL_EXPIRES_AFTER + timedelta(minutes=1)
        assert {r["action_id"]: r for r in gate.history(list(reversed(log.entries)), now=later)}[waiting]["status"] == "expired"

    asyncio.run(scenario())


@needs_mongo
def test_the_log_api_shows_only_the_callers_own_actions():
    async def scenario(db):
        log = ActionLogModel(db)
        alice_id, bob_id = ObjectId(), ObjectId()
        mine = await approved_action(log, user=str(alice_id))
        await approved_action(log, user=str(bob_id), params={**EMAIL, "subject": "Bob's secret subject"})
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(action_routes.actions_router)
        app.state.services = SimpleNamespace(mongo_db=db)
        app.dependency_overrides[get_current_user] = lambda: {"_id": alice_id}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            rows = (await client.get("/api/v1/actions/log")).json()["actions"]
        assert [r["action_id"] for r in rows] == [mine] and "Bob" not in str(rows) and "SECRET" not in str(rows)
        assert rows[0]["status"] == "approved"

    run_with_db(scenario)


# ── the real nodes ──────────────────────────────────────────────────────────

class FlakyLog:
    """The real log, with a failure injected at one point: before claiming, or while recording the outcome."""

    def __init__(self, real, fail_on_event=None):
        self.real, self.fail_on_event = real, fail_on_event

    async def append(self, entry):
        if entry["event"] == self.fail_on_event:
            self.fail_on_event = None                         # fail once, like a crash, then behave
            raise ConnectionError("injected failure")
        return await self.real.append(entry)

    async def events(self, user_id, action_id):
        return await self.real.events(user_id, action_id)


class FakeGmail:
    def __init__(self):
        self.sent = []

    def send_email(self, to, subject, body):
        self.sent.append((to, subject, body))
        return {"message_id": f"msg-{len(self.sent)}", "to": to, "subject": subject, "status": "sent"}


@needs_mongo
def test_a_failure_after_gmail_accepts_but_before_we_record_never_sends_twice_in_100_runs(monkeypatch):
    """The acceptance test. Each run: approve an email, let the node send it, kill the recording of the result, then
    retry the same action again and again (also after the lease runs out). Gmail must have been called exactly once."""
    monkeypatch.setattr(gate, "EXECUTING_LEASE_SECONDS", 0)       # a "dead" attempt is recognised straight away

    async def scenario(db):
        real = ActionLogModel(db)
        sends = []
        for run_number in range(100):
            gmail = FakeGmail()
            monkeypatch.setattr(action_nodes, "gmail_for_request", lambda gmail=gmail: gmail)
            log = FlakyLog(real, fail_on_event="executed")
            state = {"action_details": dict(EMAIL), "metadata": {}, "user_id": "u1"}
            deps, state = await approve(state, "send_email", log=log)

            with pytest.raises(ConnectionError):                   # the crash: Gmail accepted, recording failed
                await action_nodes.send_email_node(state, deps)
            assert len(gmail.sent) == 1

            for _ in range(random.Random(run_number).randint(1, 4)):   # the user / a retry asks again
                again = await action_nodes.send_email_node(state, deps)
                assert again["status"] == "error" and "check your sent folder" in again["result"].lower()
            sends.append(len(gmail.sent))
        assert sends == [1] * 100

    run_with_db(scenario)


@needs_mongo
def test_a_failure_at_any_point_sends_at_most_one_email_and_says_so(monkeypatch):
    monkeypatch.setattr(gate, "EXECUTING_LEASE_SECONDS", 0)

    async def scenario(db):
        real = ActionLogModel(db)
        for fail_on in ("executing", "executed", None):
            gmail = FakeGmail()
            monkeypatch.setattr(action_nodes, "gmail_for_request", lambda gmail=gmail: gmail)
            log = FlakyLog(real, fail_on_event=fail_on)
            deps, state = await approve({"action_details": dict(EMAIL), "metadata": {}, "user_id": "u1"}, "send_email", log=log)
            try:
                await action_nodes.send_email_node(state, deps)
            except ConnectionError:
                pass
            for _ in range(3):
                await action_nodes.send_email_node(state, deps)
            assert len(gmail.sent) <= 1, fail_on
            if fail_on == "executing":        # failed before the claim: nothing was sent, and a retry may send it
                assert len(gmail.sent) == 1
            if fail_on is None:
                assert len(gmail.sent) == 1

    run_with_db(scenario)


def test_approving_an_email_twice_sends_one_and_the_second_reply_is_the_stored_result(monkeypatch):
    gmail = FakeGmail()
    monkeypatch.setattr(action_nodes, "gmail_for_request", lambda: gmail)

    async def scenario():
        deps, state = await approve({"action_details": dict(EMAIL), "metadata": {}, "user_id": "u1"}, "send_email")
        first = await action_nodes.send_email_node(state, deps)
        second = await action_nodes.send_email_node(dict(state, metadata=dict(state["metadata"])), deps)
        assert first["status"] == second["status"] == "success" and len(gmail.sent) == 1
        assert second["metadata"]["message_id"] == "msg-1" and second["metadata"]["action_replayed"] is True

    asyncio.run(scenario())


def test_a_node_without_an_approval_does_nothing(monkeypatch):
    gmail = FakeGmail()
    monkeypatch.setattr(action_nodes, "gmail_for_request", lambda: gmail)

    async def scenario():
        for deps, state in ((AgentDeps(action_log=FakeActionLog()), {"action_details": dict(EMAIL), "metadata": {}}),
                            (AgentDeps(), {"action_details": dict(EMAIL), "metadata": {"action_id": "x"}}),
                            (AgentDeps(action_log=FakeActionLog()), {"action_details": dict(EMAIL), "metadata": {"action_id": "x"}})):
            result = await action_nodes.send_email_node(state, deps)
            assert result["status"] == "error" and result["metadata"]["error_code"] == "not_approved"
        assert gmail.sent == []

    asyncio.run(scenario())


def test_a_calendar_event_is_created_under_the_same_id_on_every_attempt(monkeypatch):
    """A crash after Google created the event, before we recorded it: the retry creates it under the same id, which
    Google answers with the event already there, so there is one event."""
    events = {}

    class FakeCalendar:
        def create_event(self, title, date, time, attendees, time_zone, event_id):
            events.setdefault(event_id, {"event_id": event_id, "title": title, "date": date, "time": time, "link": ""})
            return events[event_id]

    monkeypatch.setattr(action_nodes, "calendar_for_request", lambda: FakeCalendar())

    async def scenario():
        real_log = FakeActionLog()
        log = FlakyLog(real_log, fail_on_event="executed")
        details = {"title": "Review", "date": "2026-12-01", "time": "14:00"}
        deps, state = await approve({"action_details": details, "metadata": {}, "user_id": "u1"}, "create_calendar_event", log=log)
        with pytest.raises(ConnectionError):
            await action_nodes.create_calendar_event_node(state, deps)
        again = await action_nodes.create_calendar_event_node(state, deps)
        assert again["status"] == "success" and len(events) == 1
        assert list(events)[0] == google_event_id(state["metadata"]["action_id"])

    asyncio.run(scenario())


def test_creating_the_same_calendar_event_id_twice_returns_the_first_event():
    from stores.calendar.CalendarService import CalendarService

    class Conflict(Exception):
        resp = SimpleNamespace(status=409)

    class Insert:
        def execute(self):
            raise Conflict("The requested identifier already exists.")

    class Get:
        def execute(self):
            return {"id": "ev123", "htmlLink": "https://calendar.example/ev123"}

    class Events:
        def insert(self, **kwargs):
            assert kwargs["body"]["id"] == "ev123"
            return Insert()

        def get(self, calendarId, eventId):
            assert eventId == "ev123"
            return Get()

    service = CalendarService()
    service.service = SimpleNamespace(events=lambda: Events())
    created = service.create_event("Review", "2026-12-01", "14:00", event_id="ev123")
    assert created["event_id"] == "ev123" and created["link"].endswith("ev123")

    class Down(Exception):
        resp = SimpleNamespace(status=500)

    class FailingInsert:
        def execute(self):
            raise Down("backend error")

    service.service = SimpleNamespace(events=lambda: SimpleNamespace(insert=lambda **kw: FailingInsert()))
    with pytest.raises(ExternalServiceError) as caught:
        service.create_event("Review", "2026-12-01", "14:00", event_id="ev123")
    assert caught.value.http_status == 500                       # the gate sees it as "unclear", not "refused"


@needs_mongo
def test_a_reminder_is_saved_once_however_often_its_approval_is_replayed():
    async def scenario(db):
        real = ActionLogModel(db)
        log = FlakyLog(real, fail_on_event="executed")             # saved, then the recording fails
        state = {"query": "remind me", "user_id": "alice", "user_timezone": "UTC", "project_id": "default",
                 "priority": "medium", "metadata": {}, "action_details": {"task": "call Sara", "date": "2026-12-01", "time": "10:00"}}
        deps, state = await approve(state, "save_reminder", log=log)
        deps.db_client = db
        with pytest.raises(ConnectionError):
            await action_nodes.save_reminder_node(state, deps)
        for _ in range(3):
            assert (await action_nodes.save_reminder_node(state, deps))["status"] == "success"
        assert await db["reminders"].count_documents({"user_id": "alice"}) == 1
        saved = await db["reminders"].find_one({"user_id": "alice"})
        assert saved["action_id"] == state["metadata"]["action_id"]

    run_with_db(scenario)
