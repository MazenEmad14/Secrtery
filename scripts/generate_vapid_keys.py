"""Print a VAPID key pair for Web Push notifications. Paste the two lines into src/.env.

    python scripts/generate_vapid_keys.py

The private key is a secret: never commit it. Changing the pair later makes every existing browser
subscription invalid (users switch Web Push off and on again in Settings).
"""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


vapid = Vapid()
vapid.generate_keys()
private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(f"VAPID_PUBLIC_KEY={b64url(public)}")
print(f"VAPID_PRIVATE_KEY={b64url(private)}")
print("VAPID_SUBJECT=mailto:you@example.com   # replace with a real contact")
