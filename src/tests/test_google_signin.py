"""Continue with Google: signing in or up with a Google account (no Google servers are contacted).
Runs against a throwaway MongoDB database; skipped when none is reachable."""
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import google_auth
from helpers.config import get_settings
from helpers.error_handlers import install_error_handlers
from models.indexes import ensure_all_indexes
from routes import auth as auth_routes, local_auth

MONGO_URL = "mongodb://localhost:27017/"
FRONTEND = get_settings().FRONTEND_URL
PASSWORD = "correct horse 42"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


class FakeFlow:
    """Stands in for google_auth_oauthlib's Flow."""
    requested_scopes = None
    fail_exchange = False

    def __init__(self, scopes):
        FakeFlow.requested_scopes = scopes
        self.redirect_uri = None
        self.client_config = {"client_id": "client-id"}
        self.credentials = SimpleNamespace(
            id_token="signed-by-google", scopes=list(scopes),
            to_json=lambda: json.dumps({"token": "access", "refresh_token": "refresh", "client_id": "c",
                                        "client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token",
                                        "id_token": "signed-by-google"}))

    def authorization_url(self, **kwargs):
        return f"https://accounts.google.com/o/oauth2/auth?state=s-{uuid.uuid4().hex[:8]}", f"s-{uuid.uuid4().hex[:8]}"

    def fetch_token(self, code):
        if FakeFlow.fail_exchange:
            raise RuntimeError("invalid_grant")


VERIFY_CALLS: list = []   # the clock tolerance each Google token check was made with


@pytest.fixture
def identity(monkeypatch):
    """What Google says about the person: edit the dict in a test to change the verified identity."""
    claims = {"email": "Sara@Example.com", "email_verified": True, "sub": "google-sub-1", "name": "Sara Ahmed"}
    monkeypatch.setattr(auth_routes, "_new_flow", lambda scopes=None: FakeFlow(scopes or auth_routes.SCOPES))
    VERIFY_CALLS.clear()

    def verify(token, request, audience, clock_skew_in_seconds=0):
        VERIFY_CALLS.append(clock_skew_in_seconds)      # how the token was checked: with how much clock tolerance
        return dict(claims)

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", verify)
    monkeypatch.setattr(auth_routes.CREDENTIALS_PATH.__class__, "exists", lambda self: True)
    FakeFlow.fail_exchange = False
    return claims


@pytest.fixture
def client(identity):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(auth_routes.auth_router)
    app.include_router(local_auth.local_auth_router)
    db_name = f"test_gsignin_{uuid.uuid4().hex[:8]}"

    @app.on_event("startup")
    async def start():
        conn = AsyncIOMotorClient(MONGO_URL)
        app.state.services = SimpleNamespace(mongo_conn=conn, mongo_db=conn[db_name])
        await ensure_all_indexes(app.state.services.mongo_db)

    @app.on_event("shutdown")
    async def stop():
        await app.state.services.mongo_conn.drop_database(db_name)
        app.state.services.mongo_conn.close()

    with TestClient(app, base_url="http://localhost", follow_redirects=False) as test_client:
        test_client.db = lambda: app.state.services.mongo_db
        yield test_client


def begin(client):
    """Visit /google/signin as a visitor and return the state Google would send back."""
    response = client.get("/api/v1/auth/google/signin")
    assert response.status_code == 200, response.text
    return next(iter(auth_routes._pending_logins))


def finish(client, state, **extra):
    return client.get("/api/v1/auth/google/callback", params={"state": state, "code": "the-code", **extra})


def users(client):
    return client.portal.call(lambda: client.db().users.find({}).to_list(None))


@needs_mongo
def test_the_google_token_is_checked_with_a_small_clock_tolerance(client):
    """Real failure: Google's token was stamped one second ahead of this computer's clock and sign-in failed with
    "Token used too early". A few seconds of difference between two clocks is normal and must not block sign-in."""
    auth_routes._pending_logins.clear()
    assert finish(client, begin(client)).headers["location"] == f"{FRONTEND}/workspace"
    assert VERIFY_CALLS == [auth_routes.GOOGLE_CLOCK_SKEW_SECONDS] and 5 <= VERIFY_CALLS[0] <= 30


def test_google_accepts_a_token_issued_a_second_in_the_future_only_with_the_tolerance():
    """The real library, on a real signed token: not a stand-in. Without the tolerance this is the exact error from the
    log; with it the token is accepted, and one an hour ahead (a really wrong clock) is still refused."""
    import time

    import google.auth.crypt
    import google.auth.jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import exceptions as google_exceptions

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = google.auth.crypt.RSASigner.from_string(pem, "key-1")
    certs = {"key-1": key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()}

    def token(issued_in_seconds):
        now = int(time.time()) + issued_in_seconds
        return google.auth.jwt.encode(signer, {"iat": now, "exp": now + 3600, "aud": "client", "sub": "1"}, key_id="key-1")

    def decode(raw, skew):
        return google.auth.jwt.decode(raw, certs=certs, audience="client", clock_skew_in_seconds=skew)

    with pytest.raises(google_exceptions.InvalidValue, match="Token used too early"):
        decode(token(1), 0)                                                # what the log showed
    assert decode(token(1), auth_routes.GOOGLE_CLOCK_SKEW_SECONDS)["sub"] == "1"
    with pytest.raises(google_exceptions.InvalidValue):
        decode(token(3600), auth_routes.GOOGLE_CLOCK_SKEW_SECONDS)         # a clock an hour wrong is still refused


@needs_mongo
def test_a_visitor_can_start_google_sign_in_and_it_asks_for_identity_and_every_service(client):
    auth_routes._pending_logins.clear()
    response = client.get("/api/v1/auth/google/signin")
    assert response.status_code == 200 and "accounts.google.com" in response.json()["auth_url"]
    assert set(FakeFlow.requested_scopes) == set(google_auth.SIGN_IN_SCOPES)
    assert "openid" in FakeFlow.requested_scopes and google_auth.DRIVE_READ_SCOPE in FakeFlow.requested_scopes


@needs_mongo
def test_the_first_google_sign_in_creates_the_account_and_signs_in(client):
    auth_routes._pending_logins.clear()
    response = finish(client, begin(client))

    assert response.status_code in (302, 307) and response.headers["location"] == f"{FRONTEND}/workspace"
    assert "access_token" in client.cookies and "csrf_token" in client.cookies

    (user,) = users(client)
    assert user["email"] == "sara@example.com" and user["name"] == "Sara Ahmed" and user["google_sub"] == "google-sub-1"
    assert "password_hash" not in user and user["is_google_connected"] is True
    assert user["google_token"]                                               # stored (encrypted when a key is set)

    me = client.get("/api/v1/auth/local/me")
    assert me.status_code == 200 and me.json()["user"]["email"] == "sara@example.com"
    status = client.get("/api/v1/auth/google/status").json()
    assert status["authenticated"] is True and status["services"] == {"gmail": True, "calendar": True, "drive": True}


@needs_mongo
def test_signing_in_with_google_again_uses_the_same_account(client):
    auth_routes._pending_logins.clear()
    finish(client, begin(client))
    client.cookies.clear()
    response = finish(client, begin(client))

    assert response.headers["location"] == f"{FRONTEND}/workspace" and "access_token" in client.cookies
    assert len(users(client)) == 1


@needs_mongo
def test_a_password_account_with_the_same_address_is_not_taken_over(client, identity):
    auth_routes._pending_logins.clear()
    signup = client.post("/api/v1/auth/local/signup", json={"name": "Sara", "email": "sara@example.com", "password": PASSWORD},
                         headers={"Origin": FRONTEND})
    assert signup.status_code == 200
    client.cookies.clear()

    response = finish(client, begin(client))

    assert response.headers["location"] == f"{FRONTEND}/auth?google_error=exists"
    assert "access_token" not in client.cookies
    (user,) = users(client)
    assert not user.get("google_token") and "google_sub" not in user          # nothing was linked


@needs_mongo
def test_an_unverified_google_address_is_refused(client, identity):
    auth_routes._pending_logins.clear()
    identity["email_verified"] = False
    response = finish(client, begin(client))
    assert response.headers["location"] == f"{FRONTEND}/auth?google_error=unverified"
    assert users(client) == [] and "access_token" not in client.cookies


@needs_mongo
def test_cancelling_on_googles_page_returns_to_the_sign_in_page(client):
    auth_routes._pending_logins.clear()
    state = begin(client)
    response = client.get("/api/v1/auth/google/callback", params={"state": state, "error": "access_denied"})
    assert response.headers["location"] == f"{FRONTEND}/auth?google_error=denied"
    assert users(client) == []


@needs_mongo
def test_a_failed_token_exchange_returns_to_the_sign_in_page_not_a_json_error(client):
    auth_routes._pending_logins.clear()
    state = begin(client)
    FakeFlow.fail_exchange = True
    response = finish(client, state)
    assert response.status_code in (302, 307) and response.headers["location"] == f"{FRONTEND}/auth?google_error=failed"
    assert users(client) == []


@needs_mongo
def test_an_unknown_or_reused_state_is_rejected_and_creates_nothing(client):
    auth_routes._pending_logins.clear()
    assert finish(client, "forged-state").status_code == 400
    state = begin(client)
    assert finish(client, state).status_code in (302, 307)
    assert finish(client, state).status_code == 400                           # a state works once
    assert len(users(client)) == 1


@needs_mongo
def test_an_account_made_with_google_has_no_password_to_guess(client):
    auth_routes._pending_logins.clear()
    finish(client, begin(client))
    client.cookies.clear()
    for password in ("", "password", PASSWORD):
        response = client.post("/api/v1/auth/local/signin", json={"email": "sara@example.com", "password": password},
                               headers={"Origin": FRONTEND})
        assert response.status_code in (401, 422)
    signup = client.post("/api/v1/auth/local/signup", json={"name": "Imposter", "email": "sara@example.com", "password": PASSWORD},
                         headers={"Origin": FRONTEND})
    assert signup.status_code == 409


@needs_mongo
def test_connecting_google_from_settings_still_needs_a_signed_in_user(client):
    response = client.get("/api/v1/auth/google/login")
    assert response.status_code == 401
