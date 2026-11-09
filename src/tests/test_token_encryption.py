"""Mandatory encryption of stored Google tokens (PR 0.3): the app refuses to start without a key, nothing is ever
stored in the clear, existing plaintext tokens are encrypted at startup, and keys can be rotated.
Database tests use a throwaway MongoDB database and are skipped when none is reachable."""
import asyncio
import logging
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from helpers import token_crypto
from helpers.config import ConfigurationError, get_settings
from models.UserModel import UserModel
from models.indexes import ensure_all_indexes
from test_config import make
from test_lifespan import isolated_settings, needs_mongo  # noqa: F401  (the fixture is re-used)

MONGO_URL = "mongodb://localhost:27017/"
GOOGLE_TOKEN = {"token": "ya29.SECRET-ACCESS", "refresh_token": "1//SECRET-REFRESH", "client_id": "c", "client_secret": "s"}


def new_key() -> str:
    return Fernet.generate_key().decode()


def use_keys(monkeypatch, primary, old=None):
    settings = get_settings()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", primary)
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_OLD_KEYS", old)


def run_with_db(coro_fn):
    async def runner():
        conn = AsyncIOMotorClient(MONGO_URL)
        name = f"test_tokens_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


# ── the key is required ─────────────────────────────────────────────────────

def test_settings_without_a_key_are_invalid_and_say_how_to_make_one():
    problems = make(TOKEN_ENCRYPTION_KEY=None).problems()
    message = next(p for p in problems if "TOKEN_ENCRYPTION_KEY must be set" in p)
    assert "Fernet.generate_key()" in message
    with pytest.raises(ConfigurationError) as exc:
        make(TOKEN_ENCRYPTION_KEY=None).ensure_valid()
    assert "python -c" in str(exc.value)


def test_a_blank_key_in_the_env_file_counts_as_missing():
    assert any("must be set" in p for p in make(TOKEN_ENCRYPTION_KEY="   ").problems())


def test_a_malformed_key_or_old_key_is_reported():
    assert any("not a valid Fernet key" in p for p in make(TOKEN_ENCRYPTION_KEY="short").problems())
    assert any("OLD_KEYS" in p for p in make(TOKEN_ENCRYPTION_OLD_KEYS=f"{new_key()},nonsense").problems())
    assert make(TOKEN_ENCRYPTION_OLD_KEYS=f"{new_key()}, {new_key()}").problems() == []


def test_the_example_env_file_says_the_key_is_required_and_how_to_rotate():
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "Required: encrypts the stored Google tokens" in text and "TOKEN_ENCRYPTION_OLD_KEYS" in text


@needs_mongo
def test_startup_refuses_without_a_key_and_names_the_fix(isolated_settings, monkeypatch):  # noqa: F811
    use_keys(monkeypatch, None)
    with pytest.raises(ConfigurationError) as exc:
        with TestClient(main.app):
            pass
    assert "TOKEN_ENCRYPTION_KEY must be set" in str(exc.value) and "Fernet.generate_key()" in str(exc.value)
    assert getattr(main.app.state, "services", None) is None  # nothing was served


def test_a_token_is_never_stored_in_the_clear_when_the_key_is_missing(monkeypatch):
    use_keys(monkeypatch, None)
    with pytest.raises(ConfigurationError):
        token_crypto.encrypt_token(GOOGLE_TOKEN)


# ── encrypt / decrypt / rotate ──────────────────────────────────────────────

def test_a_token_round_trips_and_the_stored_value_has_no_readable_content():
    stored = token_crypto.encrypt_token(GOOGLE_TOKEN)
    assert list(stored) == ["enc"] and "SECRET" not in str(stored)
    assert token_crypto.decrypt_token(stored) == GOOGLE_TOKEN
    assert token_crypto.encrypt_token(None) is None and token_crypto.decrypt_token(None) is None


def test_a_token_under_a_key_that_is_gone_reads_as_none_and_is_logged_without_content(monkeypatch, caplog):
    stored = token_crypto.encrypt_token(GOOGLE_TOKEN)
    use_keys(monkeypatch, new_key())
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        assert token_crypto.decrypt_token(stored) is None
    assert caplog.records and "SECRET" not in caplog.text and stored["enc"] not in caplog.text


def test_rotation_reads_old_tokens_writes_new_ones_and_rotate_moves_them(monkeypatch):
    old, new = new_key(), new_key()
    use_keys(monkeypatch, old)
    under_old = token_crypto.encrypt_token(GOOGLE_TOKEN)

    use_keys(monkeypatch, new, old)                                  # operator rotates: new key + old key kept
    assert token_crypto.decrypt_token(under_old) == GOOGLE_TOKEN    # still readable
    assert token_crypto.under_primary_key(under_old) is False
    moved = token_crypto.rotate(under_old)
    assert token_crypto.under_primary_key(moved) is True and moved != under_old

    use_keys(monkeypatch, new, None)                                 # old key removed afterwards
    assert token_crypto.decrypt_token(moved) == GOOGLE_TOKEN
    assert token_crypto.decrypt_token(under_old) is None and token_crypto.rotate(under_old) is None


# ── the startup migration ───────────────────────────────────────────────────

@needs_mongo
def test_startup_encrypts_plaintext_tokens_and_nothing_stays_in_the_clear(caplog):
    async def scenario(db):
        users = db["users"]
        await users.insert_many([
            {"email": "plain1@x.com", "google_token": dict(GOOGLE_TOKEN)},
            {"email": "plain2@x.com", "google_token": {**GOOGLE_TOKEN, "token": "ya29.OTHER"}},
            {"email": "already@x.com", "google_token": token_crypto.encrypt_token(GOOGLE_TOKEN)},
            {"email": "none@x.com"},
            {"email": "disconnected@x.com", "google_token": None},
        ])
        with caplog.at_level(logging.DEBUG, logger="uvicorn.error"):
            counts = await UserModel(db).protect_stored_tokens()
        assert counts == {"encrypted": 2, "rotated": 0, "unreadable": 0}

        # The acceptance check: no stored token without the encrypted wrapper, and no secret anywhere in the document.
        in_the_clear = await users.count_documents(
            {"google_token": {"$exists": True, "$nin": [None, {}]}, "google_token.enc": {"$exists": False}})
        assert in_the_clear == 0
        for doc in await users.find({"google_token": {"$ne": None}}).to_list(10):
            assert "SECRET" not in str(doc) and "OTHER" not in str(doc)
        assert await UserModel(db).get_google_token("plain1@x.com") == GOOGLE_TOKEN       # and still usable
        assert (await UserModel(db).get_google_token("plain2@x.com"))["token"] == "ya29.OTHER"
        assert (await users.find_one({"email": "none@x.com"})).get("google_token") is None
        assert (await users.find_one({"email": "disconnected@x.com"}))["google_token"] is None
        assert "SECRET" not in caplog.text and "OTHER" not in caplog.text

        again = await UserModel(db).protect_stored_tokens()                              # idempotent
        assert again == {"encrypted": 0, "rotated": 0, "unreadable": 0}

    run_with_db(scenario)


@needs_mongo
def test_startup_moves_tokens_to_the_new_key_and_reports_the_ones_nobody_can_read(monkeypatch, caplog):
    old, new, lost = new_key(), new_key(), new_key()

    async def scenario(db):
        users = db["users"]
        use_keys(monkeypatch, old)
        await users.insert_one({"email": "old@x.com", "google_token": token_crypto.encrypt_token(GOOGLE_TOKEN)})
        use_keys(monkeypatch, lost)
        await users.insert_one({"email": "lost@x.com", "google_token": token_crypto.encrypt_token(GOOGLE_TOKEN)})
        before_lost = (await users.find_one({"email": "lost@x.com"}))["google_token"]

        use_keys(monkeypatch, new, old)
        with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
            counts = await UserModel(db).protect_stored_tokens()
        assert counts == {"encrypted": 0, "rotated": 1, "unreadable": 1}
        assert "1 stored Google token(s) cannot be read" in caplog.text and "SECRET" not in caplog.text
        assert (await users.find_one({"email": "lost@x.com"}))["google_token"] == before_lost   # left untouched

        use_keys(monkeypatch, new, None)                    # the old key is no longer needed: everything is on `new`
        assert await UserModel(db).get_google_token("old@x.com") == GOOGLE_TOKEN
        assert await UserModel(db).get_google_token("lost@x.com") is None

    run_with_db(scenario)


@needs_mongo
def test_a_token_refreshed_during_the_migration_is_not_overwritten_by_the_older_copy():
    """The migration writes only if the stored value is still the one it read. Simulated by changing the stored
    token after the migration has read it but before it writes."""
    async def scenario(db):
        users = db["users"]
        await users.insert_one({"email": "race@x.com", "google_token": dict(GOOGLE_TOKEN)})
        model = UserModel(db)
        real_update = model.collection.update_one
        fresh = token_crypto.encrypt_token({"token": "fresh"})

        class Racing:
            """The real collection, except that a refresh lands just before the migration's write."""
            find = staticmethod(model.collection.find)

            @staticmethod
            async def update_one(filter_, update, *args, **kwargs):
                await users.update_one({"email": "race@x.com"}, {"$set": {"google_token": fresh}})
                return await real_update(filter_, update, *args, **kwargs)

        model.collection = Racing
        counts = await model.protect_stored_tokens()
        assert counts["encrypted"] == 0                     # the guarded write did not apply
        assert (await users.find_one({"email": "race@x.com"}))["google_token"] == fresh

    run_with_db(scenario)


@needs_mongo
def test_saving_a_token_through_the_model_stores_it_encrypted():
    async def scenario(db):
        await db["users"].insert_one({"email": "u@x.com"})
        model = UserModel(db)
        await model.update_google_token("u@x.com", GOOGLE_TOKEN)
        raw = await db["users"].find_one({"email": "u@x.com"})
        assert "enc" in raw["google_token"] and "SECRET" not in str(raw)
        assert await model.get_google_token("u@x.com") == GOOGLE_TOKEN
        assert [u["google_token"] for u in await model.list_google_users()] == [GOOGLE_TOKEN]

    run_with_db(scenario)


@needs_mongo
def test_the_real_startup_runs_the_migration(isolated_settings):  # noqa: F811
    import pymongo

    client = pymongo.MongoClient(MONGO_URL)
    client[isolated_settings.db_name]["users"].insert_one({"email": "plain@x.com", "google_token": dict(GOOGLE_TOKEN)})
    with TestClient(main.app):
        pass
    stored = client[isolated_settings.db_name]["users"].find_one({"email": "plain@x.com"})
    assert "enc" in stored["google_token"] and "SECRET" not in str(stored)
