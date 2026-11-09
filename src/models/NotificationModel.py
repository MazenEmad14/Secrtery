from datetime import datetime
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from models.BaseDataModel import BaseDataModel


class NotificationModel(BaseDataModel):
    """Notifications kept in MongoDB, so they survive a restart and reach a user who was offline.

    `dedupe_key` is unique: creating the same notification twice (two workers, a retried job) stores it once.
    """

    COLLECTION_NAME = "notifications"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("user_id", 1), ("read", 1), ("created_at", -1)], "name": "notification_user_unread"},
            {"key": [("dedupe_key", 1)], "name": "notification_dedupe", "unique": True,
             "options": {"partialFilterExpression": {"dedupe_key": {"$type": "string"}}}},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def create(self, user_id: str, kind: str, title: str, body: str = "",
                     data: Optional[dict] = None, dedupe_key: Optional[str] = None) -> tuple[Optional[dict], bool]:
        """Store a notification. Returns (notification, created); created is False if `dedupe_key` already exists."""
        doc = {"user_id": user_id, "kind": kind, "title": title, "body": body, "data": data or {},
               "read": False, "created_at": datetime.utcnow()}
        if dedupe_key:
            doc["dedupe_key"] = dedupe_key
        try:
            inserted = await self.collection.insert_one(doc)
        except DuplicateKeyError:
            return None, False
        doc["_id"] = inserted.inserted_id
        return doc, True

    async def list_for_user(self, user_id: str, unread_only: bool = False, limit: int = 50) -> list[dict]:
        query: dict = {"user_id": user_id}
        if unread_only:
            query["read"] = False
        cursor = self.collection.find(query).sort("created_at", -1).limit(limit)
        return [doc async for doc in cursor]

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        try:
            oid = ObjectId(notification_id)
        except (InvalidId, TypeError):
            return False
        result = await self.collection.update_one({"_id": oid, "user_id": user_id}, {"$set": {"read": True}})
        return result.matched_count == 1

    async def mark_all_read(self, user_id: str) -> int:
        result = await self.collection.update_many({"user_id": user_id, "read": False}, {"$set": {"read": True}})
        return result.modified_count

    @staticmethod
    def public(doc: dict) -> dict:
        return {
            "id": str(doc["_id"]), "kind": doc["kind"], "title": doc["title"], "body": doc.get("body", ""),
            "data": doc.get("data", {}), "read": doc.get("read", False),
            "created_at": doc["created_at"].isoformat() + "Z",
        }
