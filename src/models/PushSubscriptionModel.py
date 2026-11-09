from datetime import datetime

from models.BaseDataModel import BaseDataModel


class PushSubscriptionModel(BaseDataModel):
    """Browser Web Push subscriptions: one per browser (endpoint), owned by one user."""

    COLLECTION_NAME = "push_subscriptions"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("endpoint", 1)], "name": "push_endpoint", "unique": True},
            {"key": [("user_id", 1)], "name": "push_user"},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def save(self, user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        """Add or refresh a subscription. A browser that signs in as someone else moves to that user, so a
        shared computer never keeps pushing the previous user's notifications."""
        await self.collection.update_one(
            {"endpoint": endpoint},
            {"$set": {"user_id": user_id, "keys": {"p256dh": p256dh, "auth": auth}},
             "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def list_for_user(self, user_id: str) -> list[dict]:
        return [doc async for doc in self.collection.find({"user_id": user_id})]

    async def remove(self, user_id: str, endpoint: str) -> bool:
        result = await self.collection.delete_one({"user_id": user_id, "endpoint": endpoint})
        return result.deleted_count == 1

    async def remove_expired(self, endpoint: str) -> None:
        """The push service said this subscription is gone (404/410)."""
        await self.collection.delete_one({"endpoint": endpoint})
