from models.BaseDataModel import BaseDataModel
from models.db_schemes.user import User
from typing import Optional, Dict, Any
import hashlib
import logging
import secrets
from pymongo.errors import DuplicateKeyError

from helpers.exceptions import EmailAlreadyRegistered
from helpers.privacy import mask_email
from helpers import token_crypto
from helpers.token_crypto import encrypt_token, decrypt_token


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(email: str) -> str:
    return (email or '').strip().lower()

logger = logging.getLogger('uvicorn.error')

class UserModel(BaseDataModel):
    
    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.users

    COLLECTION_NAME = "users"

    @classmethod
    def index_specs(cls):
        return [{"key": [("email", 1)], "name": "email_1", "unique": True}]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def create_user(self, user: User) -> bool:
        """Insert the user. Raises EmailAlreadyRegistered if the address is taken (the unique index also
        catches two sign-ups racing); any other database error propagates."""
        user_dict = user.dict(by_alias=True, exclude={"user_id"}, exclude_none=True)
        user_dict["email"] = _normalize(user_dict["email"])
        try:
            result = await self.collection.insert_one(user_dict)
        except DuplicateKeyError as exc:
            raise EmailAlreadyRegistered() from exc
        return result.acknowledged

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        """The user, or None if there is no such user. Database errors propagate: an outage must not
        look like an unknown account (which signs people out and lets a sign-up through)."""
        return await self.collection.find_one({"email": _normalize(email)})

    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            return await self.collection.find_one({"_id": ObjectId(user_id)})
        except InvalidId:
            return None

    async def create_google_user(self, name: str, email: str, google_sub: str) -> dict:
        """A new account created by Google sign-in: no password, identified by Google's account id."""
        await self.create_user(User(name=name, email=email, google_sub=google_sub, is_google_connected=True,
                                    email_verified=True))   # Google already verified the address
        return await self.get_user_by_email(email)

    async def update_google_auth_status(self, email: str, status: bool) -> bool:
        result = await self.collection.update_one(
            {"email": _normalize(email)},
            {"$set": {"is_google_connected": status}}
        )
        return result.matched_count > 0  # a no-op update (value unchanged) is still a success

    async def update_google_token(self, email: str, token_dict: Optional[Dict[str, Any]]) -> bool:
        """Save per-user Google OAuth token dict to MongoDB. False only if there is no such user."""
        result = await self.collection.update_one(
            {"email": _normalize(email)},
            {"$set": {
                "google_token": encrypt_token(token_dict),
                "is_google_connected": token_dict is not None
            }}
        )
        return result.matched_count > 0

    async def set_timezone(self, email: str, timezone_name: str) -> bool:
        result = await self.collection.update_one(
            {"email": _normalize(email)}, {"$set": {"timezone": timezone_name}}
        )
        return result.matched_count > 0

    # ── email verification ───────────────────────────────────────────────────
    async def start_email_verification(self, user_id: str, hours: int) -> str:
        """Make a one-time verification token for the user (replacing any earlier one) and return the raw value.
        Only its hash is stored, so a copy of the database cannot verify anyone."""
        from datetime import datetime, timedelta

        from bson import ObjectId

        raw = secrets.token_urlsafe(32)
        await self.collection.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"email_verification": {"token_hash": _hash(raw), "expires_at": datetime.utcnow() + timedelta(hours=hours),
                                             "sent_at": datetime.utcnow()}}})
        return raw

    async def confirm_email(self, raw: str) -> Optional[dict]:
        """Mark the address of whoever holds this token as verified; None if the token is wrong, used or expired."""
        from datetime import datetime

        user = await self.collection.find_one_and_update(
            {"email_verification.token_hash": _hash(raw or ""), "email_verification.expires_at": {"$gt": datetime.utcnow()}},
            {"$set": {"email_verified": True}, "$unset": {"email_verification": ""}})
        return user

    async def protect_stored_tokens(self) -> dict:
        """Make sure no Google token is stored in the clear or under an old key. Safe to run on every startup.

        - plaintext tokens (from before encryption was mandatory) are encrypted;
        - tokens under a retired key (TOKEN_ENCRYPTION_OLD_KEYS) are re-encrypted with the current key;
        - tokens no configured key can read are counted and left alone: those users must reconnect Google.

        Each update only applies if the stored value is still the one that was read, so a token refreshed by a
        request at the same moment is never overwritten with an older copy. Returns counts, never token content.
        """
        counts = {"encrypted": 0, "rotated": 0, "unreadable": 0}
        cursor = self.collection.find({"google_token": {"$exists": True, "$nin": [None, {}]}}, {"google_token": 1})
        async for doc in cursor:
            stored = doc["google_token"]
            if not token_crypto.is_encrypted(stored):
                changed = await self.collection.update_one(
                    {"_id": doc["_id"], "google_token": stored}, {"$set": {"google_token": encrypt_token(stored)}})
                counts["encrypted"] += changed.modified_count
            elif not token_crypto.under_primary_key(stored):
                renewed = token_crypto.rotate(stored)
                if renewed is None:
                    counts["unreadable"] += 1
                    continue
                changed = await self.collection.update_one(
                    {"_id": doc["_id"], "google_token": stored}, {"$set": {"google_token": renewed}})
                counts["rotated"] += changed.modified_count
        if counts["encrypted"] or counts["rotated"]:
            logger.info(f"Google tokens protected at startup: {counts['encrypted']} encrypted, {counts['rotated']} moved to the current key")
        if counts["unreadable"]:
            logger.error(
                f"{counts['unreadable']} stored Google token(s) cannot be read with the configured keys "
                "(TOKEN_ENCRYPTION_KEY / TOKEN_ENCRYPTION_OLD_KEYS). Those users must reconnect Google, "
                "or restore the key that encrypted them.")
        return counts

    async def get_notification_prefs(self, user_id: str) -> dict:
        """Which optional channels the user switched on. The web app always gets the notification."""
        user = await self.get_user_by_id(user_id)
        prefs = (user or {}).get("notification_prefs") or {}
        return {"push": bool(prefs.get("push")), "email": bool(prefs.get("email"))}

    async def set_notification_prefs(self, user_id: str, changes: dict) -> dict:
        from bson import ObjectId

        updates = {f"notification_prefs.{name}": bool(value) for name, value in changes.items()
                   if name in ("push", "email")}
        if updates:
            await self.collection.update_one({"_id": ObjectId(user_id)}, {"$set": updates})
        return await self.get_notification_prefs(user_id)

    async def get_google_token(self, email: str) -> Optional[Dict[str, Any]]:
        """Retrieve per-user Google OAuth token dict from MongoDB.

        None means the user has no (readable) token. Database errors propagate, so an outage is
        not mistaken for "Google not connected".
        """
        user = await self.collection.find_one({"email": _normalize(email)}, {"google_token": 1})
        return decrypt_token(user.get("google_token")) if user else None

    async def list_google_users(self) -> list[Dict[str, Any]]:
        """Users who connected Google, with their decrypted token (for background jobs)."""
        cursor = self.collection.find(
            {"google_token": {"$exists": True, "$ne": None}},
            {"email": 1, "google_token": 1, "timezone": 1},
        )
        users = []
        async for doc in cursor:
            token = decrypt_token(doc.get("google_token"))
            if token:
                users.append({
                    "user_id": str(doc["_id"]), "email": doc["email"],
                    "google_token": token, "timezone": doc.get("timezone"),
                })
        return users

    async def normalize_existing_emails(self) -> int:
        """Lower-case emails stored by older versions (which kept the case the user typed).

        An address whose lower-cased form already belongs to another account is left alone and
        reported, because merging two accounts is not something to do automatically.
        """
        changed = 0
        async for doc in self.collection.find({"email": {"$regex": "[A-Z]"}}, {"email": 1}):
            lowered = _normalize(doc["email"])
            if await self.collection.find_one({"email": lowered}, {"_id": 1}):
                logger.warning(f"Two accounts share the email {mask_email(lowered)} apart from letter case; left as is")
                continue
            await self.collection.update_one({"_id": doc["_id"]}, {"$set": {"email": lowered}})
            changed += 1
        if changed:
            logger.info(f"Lower-cased {changed} stored user emails")
        return changed
