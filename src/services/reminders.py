"""Reminders that fire: work out when a reminder is due, and turn due reminders into notifications.

A reminder stores the date and time the user meant ("2026-06-23", "14:00") in their own time zone. We convert
that once to a UTC moment (`due_at`). A scan, run as a job on the queue, finds reminders that are due and not
yet delivered and writes a notification for each.

Exactly once: the notification is stored first under a unique key (`reminder:<id>`), then the reminder is marked
delivered. A crash between the two repeats the scan step, which finds the key already taken and only marks the
reminder. Two workers scanning at once cannot both create the notification.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from helpers.config import get_settings
from helpers.timezones import resolve_timezone
from models.NotificationModel import NotificationModel
from models.UserModel import UserModel

logger = logging.getLogger("uvicorn.error")

# ReminderModel stores only the fields that were set, so a stored reminder may have no `is_completed`,
# `delivered_at` or `created_at` at all. Filters therefore say "not completed" as `$ne: True`, never `== False`.
REMINDER_KIND = "reminder"
_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def compute_due_at(date: str, time: str, timezone_name: Optional[str], created_at: Optional[datetime] = None,
                   default_hour: Optional[int] = None) -> Optional[datetime]:
    """The moment a reminder is due, as a naive UTC datetime, or None if it has no usable date or time.

    - date and time: that wall-clock time in the user's zone.
    - date only: `default_hour` (the daily-summary hour) that day.
    - time only: that time on the day it was created, in the user's zone.
    """
    zone = resolve_timezone(timezone_name)
    day = None
    if date and date.strip():
        try:
            day = datetime.strptime(date.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
    hour, minute = (get_settings().DAILY_SUMMARY_HOUR if default_hour is None else default_hour), 0
    if time and time.strip():
        match = _TIME.match(time)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if day is None:
            created = (created_at or datetime.utcnow()).replace(tzinfo=timezone.utc)
            day = created.astimezone(zone).date()
    if day is None:
        return None
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


async def _user_timezone(users: UserModel, cache: dict, user_id: str) -> Optional[str]:
    if user_id not in cache:
        user = await users.get_user_by_id(user_id)
        cache[user_id] = (user or {}).get("timezone")
    return cache[user_id]


async def backfill_due_dates(db, limit: int = 200) -> int:
    """Give reminders saved without `due_at` (older ones) a due moment. Returns how many were scheduled."""
    reminders, users, zones, scheduled = db["reminders"], UserModel(db), {}, 0
    cursor = reminders.find(
        {"due_at": None, "due_unparsed": {"$ne": True}, "delivered_at": None, "is_completed": {"$ne": True}}).limit(limit)
    async for doc in cursor:
        zone_name = await _user_timezone(users, zones, doc["user_id"]) if doc.get("user_id") else None
        due = compute_due_at(doc.get("date", ""), doc.get("time", ""), zone_name, doc.get("created_at"))
        if due is None:
            await reminders.update_one({"_id": doc["_id"]}, {"$set": {"due_unparsed": True}})
        else:
            await reminders.update_one({"_id": doc["_id"]}, {"$set": {"due_at": due}})
            scheduled += 1
    return scheduled


async def deliver_due_reminders(db, now: Optional[datetime] = None, limit: int = 500) -> int:
    """Create a notification for each due, undelivered reminder. Returns how many notifications were created."""
    now = now or datetime.utcnow()
    reminders, notifications, created = db["reminders"], NotificationModel(db), 0
    cursor = reminders.find(
        {"delivered_at": None, "is_completed": {"$ne": True}, "due_at": {"$lte": now}}).sort("due_at", 1).limit(limit)
    async for doc in cursor:
        user_id = doc.get("user_id", "")
        if not user_id:  # nobody to tell
            await reminders.update_one({"_id": doc["_id"], "delivered_at": None}, {"$set": {"delivered_at": now}})
            continue
        _, was_created = await notifications.create(
            user_id=user_id, kind=REMINDER_KIND, title=f"🔔 Reminder: {doc.get('task', '')}",
            body=doc.get("notes", ""),
            data={"reminder_id": str(doc["_id"]), "due_at": doc["due_at"].isoformat() + "Z"},
            dedupe_key=f"reminder:{doc['_id']}")
        await reminders.update_one({"_id": doc["_id"], "delivered_at": None}, {"$set": {"delivered_at": now}})
        created += int(was_created)
    return created


async def scan_due_reminders(db, now: Optional[datetime] = None) -> dict:
    scheduled = await backfill_due_dates(db)
    delivered = await deliver_due_reminders(db, now=now)
    if scheduled or delivered:
        logger.info(f"Reminder scan: scheduled {scheduled}, delivered {delivered}")
    return {"scheduled": scheduled, "delivered": delivered}
