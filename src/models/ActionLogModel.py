from datetime import datetime
from typing import Optional

from pymongo.errors import DuplicateKeyError

from models.BaseDataModel import BaseDataModel

# One action (a single email, event, reminder or file) leaves these entries, each at most once:
#   proposed   the assistant asked for approval (parameters hashed)
#   approved / rejected   the user's decision (who and when)
#   executing  the external call is about to be made (the claim that stops a second attempt)
#   executed / failed     the outcome (with the id Gmail, Calendar or Drive gave back)
#   unknown    the call may or may not have happened (a crash or timeout in between): never retried blindly
EVENTS = ("proposed", "approved", "rejected", "executing", "executed", "failed", "unknown")


class ActionLogModel(BaseDataModel):
    """The audit log of approval-gated actions. Append-only by construction: this class can add entries and read them,
    and has no way to change or delete one. (Stopping a database administrator is a deployment matter: give the
    application's database user no update or delete right on this collection.)"""

    COLLECTION_NAME = "actions_log"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("user_id", 1), ("created_at", -1)], "name": "action_log_user"},
            # An entry type happens once per action: this is what makes "claim the execution" atomic.
            {"key": [("action_id", 1), ("event", 1)], "name": "action_log_once", "unique": True},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def append(self, entry: dict) -> bool:
        """Add an entry. False if this action already has an entry of that kind (nothing is written)."""
        if entry.get("event") not in EVENTS:
            raise ValueError(f"unknown action log event {entry.get('event')!r}")
        doc = {**entry, "created_at": datetime.utcnow()}
        try:
            await self.collection.insert_one(doc)
        except DuplicateKeyError:
            return False
        return True

    async def events(self, user_id: str, action_id: str) -> list[dict]:
        """All entries of one action, oldest first. Another user's action looks like one that does not exist."""
        cursor = self.collection.find({"user_id": user_id, "action_id": action_id}).sort([("created_at", 1), ("_id", 1)])
        return [doc async for doc in cursor]

    async def recent_events(self, user_id: str, limit: int = 300, before: Optional[datetime] = None) -> list[dict]:
        query: dict = {"user_id": user_id}
        if before:
            query["created_at"] = {"$lt": before}
        cursor = self.collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(limit)
        return [doc async for doc in cursor]
