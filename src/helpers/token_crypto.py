"""Encryption at rest for stored Google OAuth tokens (mandatory).

TOKEN_ENCRYPTION_KEY (a Fernet key) is required: the app will not start without it, and saving a token without
one raises instead of quietly writing plaintext. Stored value: {"enc": "<fernet token>"}.

Rotation: set the new key as TOKEN_ENCRYPTION_KEY and keep the previous key(s) in TOKEN_ENCRYPTION_OLD_KEYS
(comma separated). Tokens are written with the new key and still read with the old ones; at startup every token
still under an old key is re-encrypted with the new key (see UserModel.protect_stored_tokens). Once startup has
logged that nothing is left under an old key, the old key can be removed.
"""
import json
import logging
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from helpers.config import ConfigurationError, get_settings

logger = logging.getLogger("uvicorn.error")

_ENC_FIELD = "enc"
GENERATE_KEY_COMMAND = 'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'


def _primary() -> Fernet:
    key = get_settings().TOKEN_ENCRYPTION_KEY
    if not key:
        raise ConfigurationError(f"TOKEN_ENCRYPTION_KEY is not set. Generate one with: {GENERATE_KEY_COMMAND}")
    return Fernet(key.encode())


def _all_keys() -> MultiFernet:
    """The primary key first (used to encrypt), then the old keys (only used to decrypt)."""
    settings = get_settings()
    old = [Fernet(k.encode()) for k in (settings.TOKEN_ENCRYPTION_OLD_KEYS or "").replace(" ", "").split(",") if k]
    return MultiFernet([_primary(), *old])


def is_encrypted(stored: Optional[Dict[str, Any]]) -> bool:
    return bool(stored) and _ENC_FIELD in stored


def encrypt_token(token: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The value to store for a token. Raises ConfigurationError when there is no key: never stores plaintext."""
    if token is None:
        return None
    return {_ENC_FIELD: _primary().encrypt(json.dumps(token).encode()).decode()}


def decrypt_token(stored: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The token, or None if there is none or it cannot be read (logged; the user must reconnect Google).

    A plaintext value (written before encryption was mandatory) is still returned, but startup encrypts them all,
    so none should be left."""
    if not stored:
        return None
    if _ENC_FIELD not in stored:
        return stored
    try:
        return json.loads(_all_keys().decrypt(stored[_ENC_FIELD].encode()))
    except InvalidToken:
        logger.error("Could not decrypt a stored Google token (it was encrypted with a key that is no longer configured)")
        return None


def under_primary_key(stored: Dict[str, Any]) -> bool:
    """True if this encrypted value was made with the current TOKEN_ENCRYPTION_KEY."""
    try:
        _primary().decrypt(stored[_ENC_FIELD].encode())
        return True
    except InvalidToken:
        return False


def rotate(stored: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Re-encrypt a value under the primary key; None if none of the configured keys can read it."""
    try:
        return {_ENC_FIELD: _all_keys().rotate(stored[_ENC_FIELD].encode()).decode()}
    except InvalidToken:
        return None
