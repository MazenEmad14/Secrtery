"""Safety limits and shadow usage metering (PR 0.5): every model call is counted per user per day, a per-user daily
cost ceiling stops a runaway loop, and chat/actions/voice have per-user rate limits. Uses a throwaway MongoDB
database; skipped when none is reachable."""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from bson import ObjectId
from fastapi import Depends, FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.llm_schemas import UrgentVerdict
from helpers import rate_limits
from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.error_handlers import install_error_handlers
from helpers.exceptions import ExternalServiceError, UsageLimitReached
from helpers.llm_json import llm_json
from models.UsageModel import UsageModel, today_utc
from models.indexes import ensure_all_indexes
from routes import agent as agent_routes, usage as usage_routes
from services.jobs import JobContext, JobWorker
from services.usage import UsageMeter, as_user, current_user, estimate_tokens
from stores.llm.MeteredLLM import MeteredLLM
from test_config import make
from test_error_handling import RaisingGraph

MONGO_URL = "mongodb://localhost:27017/"


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
        name = f"test_usage_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


class FakeProvider:
    """A model client that answers with `reply` and counts the calls that really reach it."""

    def __init__(self, reply="x" * 300, error=None):
        self.reply, self.error, self.calls = reply, error, 0
        self.generation_model_id = "fake-model"
        self.embedding_size = 8

    async def agenerate_text(self, prompt, chat_history=None, max_output_tokens=None, temperature=None, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply

    def generate_text(self, prompt, chat_history=None, max_output_tokens=None, temperature=None, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply

    async def aembed_texts(self, texts, document_type=None):
        self.calls += 1
        return [[0.0] * 8 for _ in texts]

    def embed_texts(self, texts, document_type=None):
        self.calls += 1
        return [[0.0] * 8 for _ in texts]

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}


def settings_with(monkeypatch, **changes):
    settings = get_settings()
    for name, value in changes.items():
        monkeypatch.setattr(settings, name, value)
    return settings


# ── the numbers ─────────────────────────────────────────────────────────────

def test_token_estimates_are_generous_and_count_history():
    assert estimate_tokens("") == 0 and estimate_tokens("abc") == 1 and estimate_tokens("a" * 301) == 101
    history = [{"role": "user", "content": "x" * 30}, {"role": "assistant", "content": "y" * 30}]
    assert estimate_tokens("p" * 30, history) == 30
    assert estimate_tokens("ب" * 30) == 10          # Arabic is counted by characters too


def test_the_user_is_set_for_background_work_and_restored():
    assert current_user() == ""
    with as_user("u1"):
        assert current_user() == "u1"
    assert current_user() == ""


def test_usage_counters_add_up_exactly_under_concurrency_and_stay_per_user():
    async def scenario(db):
        usage = UsageModel(db)
        await asyncio.gather(*(usage.add("u1", {"llm_calls": 1, "input_tokens": 10, "est_cost_usd": 0.001}) for _ in range(50)))
        await usage.add("u2", {"llm_calls": 1})
        one = await usage.get_day("u1")
        assert one["llm_calls"] == 50 and one["input_tokens"] == 500 and one["est_cost_usd"] == pytest.approx(0.05)
        assert (await usage.get_day("u2"))["llm_calls"] == 1 and (await usage.get_day("nobody"))["llm_calls"] == 0
        assert await db["usage"].count_documents({"user_id": "u1"}) == 1          # one document per user per day

    run_with_db(scenario)


# ── metering every model call ───────────────────────────────────────────────

def test_a_generation_call_is_counted_against_the_user_it_is_for(monkeypatch):
    settings_with(monkeypatch, LLM_COST_PER_1K_INPUT_TOKENS_USD=0.001, LLM_COST_PER_1K_OUTPUT_TOKENS_USD=0.002)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        provider = FakeProvider(reply="y" * 300)
        llm = MeteredLLM(provider, meter)
        with as_user("alice"):
            assert await llm.agenerate_text(prompt="p" * 600, chat_history=[{"role": "user", "content": "h" * 300}]) == "y" * 300
        with as_user("bob"):
            await llm.agenerate_text(prompt="p" * 30)
        alice = await UsageModel(db).get_day("alice")
        assert alice["llm_calls"] == 1 and alice["input_tokens"] == 300 and alice["output_tokens"] == 100
        assert alice["est_cost_usd"] == pytest.approx(300 * 0.001 / 1000 + 100 * 0.002 / 1000)
        assert (await UsageModel(db).get_day("bob"))["input_tokens"] == 10
        assert llm.generation_model_id == "fake-model" and llm.construct_prompt("a", "user")["content"] == "a"   # provider's own attributes

    run_with_db(scenario)


def test_a_failing_model_call_still_counts_its_input_so_a_retry_storm_is_not_free(monkeypatch):
    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        llm = MeteredLLM(FakeProvider(error=ExternalServiceError("down")), meter)
        with as_user("alice"):
            for _ in range(3):
                with pytest.raises(ExternalServiceError):
                    await llm.agenerate_text(prompt="p" * 90)
        day = await UsageModel(db).get_day("alice")
        assert day["llm_calls"] == 3 and day["input_tokens"] == 90 and day["output_tokens"] == 0

    run_with_db(scenario)


def test_embeddings_and_transcription_and_whatsapp_are_counted(monkeypatch):
    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        llm = MeteredLLM(FakeProvider(), meter)
        with as_user("alice"):
            await llm.aembed_texts(["a" * 30, "b" * 30])
        await meter.record_transcription("alice", 90.0)
        await meter.record_whatsapp_message("alice")
        await meter.record_whatsapp_message("alice", 2)
        day = await UsageModel(db).get_day("alice")
        assert day["embedding_calls"] == 1 and day["embedding_tokens"] == 20
        assert day["transcription_seconds"] == 90 and day["whatsapp_messages"] == 3
        assert day["est_cost_usd"] > 0

    run_with_db(scenario)


def test_model_calls_from_worker_threads_are_counted_and_stopped_too(monkeypatch):
    """The sync controllers run in worker threads; their calls go through the same meter and ceiling."""
    settings_with(monkeypatch, USER_DAILY_COST_CEILING_USD=0.00001, LLM_COST_PER_1K_INPUT_TOKENS_USD=0.01)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        provider = FakeProvider()
        llm = MeteredLLM(provider, meter)

        with as_user("alice"):
            reply = await asyncio.to_thread(llm.generate_text, "p" * 300)       # first call: allowed and counted
            assert reply
            await asyncio.sleep(0.1)
            assert (await UsageModel(db).get_day("alice"))["llm_calls"] == 1
            with pytest.raises(UsageLimitReached):
                await asyncio.to_thread(llm.generate_text, "again")             # over the ceiling: refused in the thread too
            with pytest.raises(UsageLimitReached):
                await asyncio.to_thread(llm.embed_texts, ["t"])                 # and so is an embedding
        assert provider.calls == 1                                               # the refused calls never reached the provider

    run_with_db(scenario)


# ── the daily ceiling ───────────────────────────────────────────────────────

def test_a_runaway_loop_is_stopped_by_the_daily_ceiling(monkeypatch):
    """The acceptance test: code that calls the model in an endless loop for one user stops once their estimated
    spend reaches the ceiling, long before it could run for ever, and other users are not affected."""
    settings_with(monkeypatch, USER_DAILY_COST_CEILING_USD=0.05,
                  LLM_COST_PER_1K_INPUT_TOKENS_USD=0.01, LLM_COST_PER_1K_OUTPUT_TOKENS_USD=0.03)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        provider = FakeProvider(reply="y" * 300)
        llm = MeteredLLM(provider, meter)
        iterations = 0
        with as_user("runaway"):
            with pytest.raises(UsageLimitReached) as stopped:
                while True:                                  # "while True": only the ceiling can end this
                    iterations += 1
                    assert iterations < 10_000, "the loop was never stopped"
                    await llm.agenerate_text(prompt="p" * 600)

        # Each call is about $0.005 (200 input + 100 output tokens): the ceiling of $0.05 stops it after ~10 calls.
        assert 5 <= provider.calls <= 15, provider.calls
        spent = await UsageModel(db).cost_today("runaway")
        assert 0.05 <= spent < 0.05 + 0.01                   # over by at most the call that crossed the line
        assert "usage limit" in stopped.value.public_message and "midnight UTC" in stopped.value.public_message
        assert "reminders" in stopped.value.public_message   # says what keeps working

        calls_before = provider.calls
        with as_user("runaway"), pytest.raises(UsageLimitReached):
            await llm.agenerate_text(prompt="again")        # still paused, and the provider is not called
        assert provider.calls == calls_before

        with as_user("someone-else"):                        # nobody else is affected
            assert await llm.agenerate_text(prompt="hi")
        summary = await meter.summary("runaway")
        assert summary["paused"] is True and summary["ceiling_used_fraction"] == 1.0

    run_with_db(scenario)


def test_the_ceiling_also_stops_the_json_helper_every_agent_node_uses(monkeypatch):
    settings_with(monkeypatch, USER_DAILY_COST_CEILING_USD=0.001, LLM_COST_PER_1K_INPUT_TOKENS_USD=0.01)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        provider = FakeProvider(reply='{"is_urgent": false, "message": ""}')
        llm = MeteredLLM(provider, meter)
        with as_user("alice"):
            assert (await llm_json(llm, "p" * 600, UrgentVerdict)).is_urgent is False
            with pytest.raises(UsageLimitReached):
                await llm_json(llm, "p" * 600, UrgentVerdict)

    run_with_db(scenario)


def test_without_a_user_nothing_is_stopped_and_a_database_problem_does_not_stop_chat(monkeypatch):
    settings_with(monkeypatch, USER_DAILY_COST_CEILING_USD=0.0000001)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        llm = MeteredLLM(FakeProvider(), meter)
        for _ in range(3):
            assert await llm.agenerate_text(prompt="p" * 600)           # no user: recorded as "system", never refused
        assert (await UsageModel(db).get_day("system"))["llm_calls"] == 3

        class BrokenUsage:
            async def cost_today(self, user_id):
                raise ConnectionError("db down")

            async def add(self, *a, **k):
                raise ConnectionError("db down")

        meter.usage = BrokenUsage()
        with as_user("alice"):
            assert await llm.agenerate_text(prompt="hi")                # the safety net fails open, and says so in the log

    run_with_db(scenario)


def test_a_job_runs_as_the_user_who_queued_it(monkeypatch):
    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        llm = MeteredLLM(FakeProvider(), meter)
        seen = {}

        async def handler(context, job, progress):
            seen["user"] = current_user()
            await llm.aembed_texts(["chunk"] * 3)
            return {"ok": True}

        jobs = SimpleNamespace(JOB_CONCURRENCY=1, JOB_POLL_SECONDS=0.05, JOB_LEASE_SECONDS=30, JOB_RETENTION_DAYS=1, JOB_MAX_ATTEMPTS=1)
        from models.JobModel import JobModel

        await JobModel(db).enqueue("t", "alice", "p", "p", {}, 1)
        worker = JobWorker(JobContext(db, None, None, None, None), db, jobs, handlers={"t": handler}, worker_id="w")
        task = asyncio.create_task(worker.run())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if seen:
                break
        await asyncio.sleep(0.2)
        await worker.stop()
        await task
        assert seen["user"] == "alice" and (await UsageModel(db).get_day("alice"))["embedding_calls"] == 1

    run_with_db(scenario)


def test_background_checks_run_as_the_user_they_check(monkeypatch):
    from controllers import proactive_agent

    seen = []

    async def fake_evaluate(services, user):
        seen.append(current_user())

    monkeypatch.setattr(proactive_agent, "_evaluate_user", fake_evaluate)

    class Users:
        @classmethod
        async def create_instance(cls, db):
            return cls()

        async def list_google_users(self):
            return [{"user_id": "u1", "email": "a@x.com"}, {"user_id": "u2", "email": "b@x.com"}]

    monkeypatch.setattr(proactive_agent, "UserModel", Users)
    asyncio.run(proactive_agent.evaluate_inbox_and_notify(SimpleNamespace(mongo_db=None)))
    assert seen == ["u1", "u2"] and current_user() == ""


# ── rate limits ─────────────────────────────────────────────────────────────

def limited_app(db, user, meter=None):
    app = FastAPI()
    install_error_handlers(app)
    app.state.services = SimpleNamespace(mongo_db=db, usage=meter)
    app.dependency_overrides[get_current_user] = lambda: user

    @app.post("/chat", dependencies=[Depends(rate_limits.chat_limit)])
    async def chat():
        return {"ok": True}

    @app.post("/resume", dependencies=[Depends(rate_limits.action_limit)])
    async def resume():
        return {"ok": True}

    @app.post("/voice", dependencies=[Depends(rate_limits.voice_limit)])
    async def voice():
        return {"ok": True}

    return app


def http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_chat_is_limited_per_user_with_a_clear_message_and_retry_after(monkeypatch):
    settings_with(monkeypatch, CHAT_MAX_PER_MINUTE=3, ACTION_MAX_PER_MINUTE=2, VOICE_MAX_PER_MINUTE=1)

    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        alice, bob = {"_id": ObjectId()}, {"_id": ObjectId()}
        async with http(limited_app(db, alice, meter)) as client:
            assert [(await client.post("/chat")).status_code for _ in range(3)] == [200, 200, 200]
            over = await client.post("/chat")
            assert over.status_code == 429 and over.json()["code"] == "rate_limited"
            assert "too quickly" in over.json()["detail"] and "messages" in over.json()["detail"]
            assert 1 <= int(over.headers["retry-after"]) <= 60
            # Separate buckets: chatting too fast does not block approving an action or using voice.
            assert (await client.post("/resume")).status_code == 200
            assert (await client.post("/voice")).status_code == 200
            assert (await client.post("/voice")).status_code == 429
        async with http(limited_app(db, bob, meter)) as client:
            assert (await client.post("/chat")).status_code == 200          # another user is not affected
        day = await UsageModel(db).get_day(str(alice["_id"]))
        assert day["chat_requests"] == 3 and day["action_requests"] == 1       # only allowed requests are counted

    run_with_db(scenario)


def test_the_limit_resets_after_its_window(monkeypatch):
    settings_with(monkeypatch, CHAT_MAX_PER_MINUTE=1)

    async def scenario(db):
        user = {"_id": ObjectId()}
        async with http(limited_app(db, user)) as client:
            assert (await client.post("/chat")).status_code == 200
            assert (await client.post("/chat")).status_code == 429
            await db["rate_limits"].update_many({}, {"$set": {"expires_at": datetime.utcnow() - timedelta(seconds=1)}})
            assert (await client.post("/chat")).status_code == 200

    run_with_db(scenario)


def test_the_real_chat_routes_are_limited_and_the_ceiling_message_reaches_the_user(monkeypatch):
    """The routes that cost money carry the limit, and a user over the ceiling gets the clear message (not a vague
    'something went wrong') from /chat and from the stream."""
    chat_routes = {route.path: route for route in agent_routes.agent_router.routes}
    for path in ("/api/v1/agent/chat", "/api/v1/agent/chat/stream"):
        assert rate_limits.chat_limit in [d.call for d in chat_routes[path].dependant.dependencies], path
    assert rate_limits.action_limit in [d.call for d in chat_routes["/api/v1/agent/resume"].dependant.dependencies]
    for path in ("/api/v1/agent/stt", "/api/v1/agent/tts"):
        assert rate_limits.voice_limit in [d.call for d in chat_routes[path].dependant.dependencies], path

    from models.UserModel import UserModel

    async def no_token(self, email):
        return None

    monkeypatch.setattr(UserModel, "get_google_token", no_token)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(agent_routes.agent_router)
    app.dependency_overrides[get_current_user] = lambda: {"_id": "u1", "email": "a@example.com"}
    app.dependency_overrides[agent_routes.chat_limit] = lambda: None
    app.state.services = SimpleNamespace(mongo_db=MagicMock(), agent_graph=RaisingGraph(UsageLimitReached()))
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/v1/agent/chat", json={"query": "hello", "thread_id": "t1"})
    assert response.status_code == 429 and response.json()["code"] == "daily_limit_reached"
    assert "usage limit" in response.json()["detail"]
    body = client.post("/api/v1/agent/chat/stream", json={"query": "hello", "thread_id": "t1"}).text
    assert "usage limit" in body and "daily_limit_reached" in body


# ── reading the numbers ─────────────────────────────────────────────────────

def test_a_user_can_read_their_own_usage_and_nobody_elses(monkeypatch):
    async def scenario(db):
        meter = UsageMeter(db, get_settings())
        alice, bob = {"_id": ObjectId()}, {"_id": ObjectId()}
        await meter.record_generation(str(alice["_id"]), 3000, 900)
        await meter.record_transcription(str(alice["_id"]), 30)
        await meter.record_generation(str(bob["_id"]), 5, 5)
        await meter.usage.add(str(alice["_id"]), {"llm_calls": 4}, day="2020-01-01")       # long ago: outside the window

        def app_for(user):
            app = FastAPI()
            install_error_handlers(app)
            app.include_router(usage_routes.usage_router)
            app.state.services = SimpleNamespace(mongo_db=db, usage=meter)
            app.dependency_overrides[get_current_user] = lambda: user
            return app

        async with http(app_for(alice)) as client:
            data = (await client.get("/api/v1/usage?days=7")).json()
        assert data["metering"] == "shadow" and data["today"]["input_tokens"] == 3000
        assert data["today"]["output_tokens"] == 900 and data["today"]["transcription_seconds"] == 30
        assert data["days"][0]["day"] == today_utc() and len(data["days"]) == 1
        assert 0 < data["ceiling_used_fraction"] < 1 and data["paused"] is False
        assert data["daily_ceiling_usd"] == get_settings().USER_DAILY_COST_CEILING_USD
        async with http(app_for(bob)) as client:
            assert (await client.get("/api/v1/usage")).json()["today"]["input_tokens"] == 5

    run_with_db(scenario)


# ── settings ────────────────────────────────────────────────────────────────

def test_only_shadow_metering_exists_and_the_ceiling_must_be_positive():
    assert make().USAGE_METERING == "shadow" and make().problems() == []
    assert any("USAGE_METERING" in p for p in make(USAGE_METERING="enforced").problems())
    with pytest.raises(ValueError):
        make(USER_DAILY_COST_CEILING_USD=0)
    with pytest.raises(ValueError):
        make(CHAT_MAX_PER_MINUTE=0)
