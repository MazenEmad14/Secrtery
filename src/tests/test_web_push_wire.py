"""The real Web Push message, without the internet: send_web_push talks to a stand-in push service on localhost, and the
test then opens the message the way a browser would (decrypt with the subscription's private key and check the VAPID
signature). The browsers' real push services were not contacted."""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import http_ece
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.notifier import PushGone, send_web_push


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class FakePushService:
    """Records the request; answers with `status`."""

    def __init__(self, status=201):
        outer = self
        outer.requests, outer.status = [], status

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(({k.lower(): v for k, v in self.headers.items()}, self.rfile.read(length)))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/push/abc"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def keys():
    vapid = Vapid()
    vapid.generate_keys()
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return SimpleNamespace(public=public, VAPID_PRIVATE_KEY=b64url(private), VAPID_SUBJECT="mailto:ops@example.com")


def browser_subscription(endpoint):
    """What PushManager.subscribe gives a page: an endpoint, a public key and an auth secret (we keep the private key)."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return private, {"endpoint": endpoint, "keys": {"p256dh": b64url(public), "auth": b64url(auth)}}, auth


def test_a_push_message_is_encrypted_for_the_browser_and_signed_with_the_servers_vapid_key(keys):
    service = FakePushService()
    try:
        private, subscription, auth = browser_subscription(service.url)
        payload = {"title": "🔔 Reminder: اتصل بالعميل", "body": "ملاحظات", "id": "n1"}
        send_web_push(subscription, payload, keys)

        headers, body = service.requests[0]
        # The browser decrypts it with its private key ...
        decrypted = http_ece.decrypt(body, private_key=private, auth_secret=auth, version="aes128gcm")
        assert json.loads(decrypted) == payload                      # Arabic text survives
        # ... and the push service checks who sent it: a signature that verifies with the server's public key.
        scheme, token = headers["authorization"].split(" ", 1)
        assert scheme.lower() == "vapid"
        fields = dict(part.strip().split("=", 1) for part in token.split(","))
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), keys.public)
        claims = jwt.decode(fields["t"], public_key, algorithms=["ES256"], audience=service.url.rsplit("/push", 1)[0])
        assert claims["sub"] == "mailto:ops@example.com"
        assert b64url_decode(fields["k"]) == keys.public
    finally:
        service.close()


def test_an_expired_subscription_is_reported_as_gone(keys):
    service = FakePushService(status=410)
    try:
        _, subscription, _ = browser_subscription(service.url)
        with pytest.raises(PushGone):
            send_web_push(subscription, {"title": "t"}, keys)
    finally:
        service.close()


def test_other_push_service_errors_are_not_mistaken_for_expiry(keys):
    service = FakePushService(status=500)
    try:
        _, subscription, _ = browser_subscription(service.url)
        with pytest.raises(Exception) as caught:
            send_web_push(subscription, {"title": "t"}, keys)
        assert not isinstance(caught.value, PushGone)
    finally:
        service.close()
