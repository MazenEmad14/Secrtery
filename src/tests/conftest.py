"""Settings for the whole test run.

TOKEN_ENCRYPTION_KEY is mandatory, but a developer's own src/.env may or may not have one, and tests must not depend
on it (or ever touch the real key). So every test run uses its own throwaway key. Tests that exercise a missing or
rotated key build their own Settings or patch this one.
"""
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def test_token_encryption_key(monkeypatch):
    from helpers.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_OLD_KEYS", None)
