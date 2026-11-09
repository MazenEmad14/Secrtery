from pydantic import BaseModel, Field
from typing import Optional
from bson.objectid import ObjectId
from datetime import datetime

class Reminder(BaseModel):
    id: Optional[ObjectId] = Field(None, alias="_id")
    task: str = Field(..., min_length=1)
    date: str = Field(default="")                  # e.g. "2026-06-23"
    time: str = Field(default="")                  # e.g. "14:00"
    priority: str = Field(default="medium")        # low | medium | high | critical
    notes: str = Field(default="")
    is_completed: bool = Field(default=False)
    user_id: str = Field(default="")
    project_id: str = Field(default="default")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # When it is due, in UTC (worked out from date + time in the user's zone); None = not scheduled.
    due_at: Optional[datetime] = None
    # Set once the reminder has been turned into a notification, so it is never delivered twice.
    delivered_at: Optional[datetime] = None
    # True when date/time could not be turned into a moment (so the scan stops looking at it).
    due_unparsed: bool = False

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def get_indexes(cls):
        return [
            {
                "key": [("project_id", 1)],
                "name": "reminder_project_id_index",
                "unique": False
            },
            {
                "key": [("is_completed", 1), ("date", 1)],
                "name": "reminder_status_date_index",
                "unique": False
            },
            {
                # the due-reminder scan: undelivered, not completed, due before now
                "key": [("delivered_at", 1), ("is_completed", 1), ("due_at", 1)],
                "name": "reminder_due_scan_index",
                "unique": False
            }
        ]
