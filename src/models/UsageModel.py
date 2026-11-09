from datetime import datetime, timedelta, timezone
from typing import Optional

from models.BaseDataModel import BaseDataModel

# Counters kept per user per day. All are plain numbers that only ever increase.
COUNTERS = (
    "chat_requests", "action_requests", "llm_calls", "input_tokens", "output_tokens",
    "embedding_calls", "embedding_tokens", "transcription_seconds", "whatsapp_messages", "est_cost_usd",
)


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class UsageModel(BaseDataModel):
    """What each user used each day (one document per user and UTC day). Written in shadow mode: it is the record of
    what a user costs, and what the daily safety ceiling is checked against. Token counts are estimates."""

    COLLECTION_NAME = "usage"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("user_id", 1), ("day", -1)], "name": "usage_user_day", "unique": True},
            # Old days are not needed to enforce anything; keep a year for cost analysis.
            {"key": [("created_at", 1)], "name": "usage_expiry", "options": {"expireAfterSeconds": 400 * 86400}},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def add(self, user_id: str, increments: dict, day: Optional[str] = None) -> None:
        """Add to the user's counters for the day, atomically (concurrent requests never lose each other's counts)."""
        increments = {name: value for name, value in increments.items() if name in COUNTERS and value}
        if not increments:
            return
        day = day or today_utc()
        await self.collection.update_one(
            {"user_id": user_id, "day": day},
            {"$inc": increments, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def get_day(self, user_id: str, day: Optional[str] = None) -> dict:
        doc = await self.collection.find_one({"user_id": user_id, "day": day or today_utc()}) or {}
        return {name: doc.get(name, 0) for name in COUNTERS}

    async def cost_today(self, user_id: str) -> float:
        doc = await self.collection.find_one({"user_id": user_id, "day": today_utc()}, {"est_cost_usd": 1})
        return float((doc or {}).get("est_cost_usd", 0.0))

    async def list_days(self, user_id: str, days: int = 7) -> list[dict]:
        """The user's last `days` days, newest first (days without use are left out)."""
        first = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        cursor = self.collection.find({"user_id": user_id, "day": {"$gte": first}}).sort("day", -1)
        return [{"day": doc["day"], **{name: doc.get(name, 0) for name in COUNTERS}} async for doc in cursor]
